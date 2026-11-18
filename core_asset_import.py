"""証券会社の CSV から取引を取り込む(GUI を含まない部分)。

設計は fina_asset_design_phase2.md の §3 を参照。
証券会社ごとに CSV の形式が違うため、変換は「会社ごとのアダプタ」(CsvAdapter を継承したクラス)に分け、
ADAPTERS に並べる。CSV を読むと、見出しの行から合うアダプタを自動で選ぶ。

対応している CSV:
    楽天証券「保有商品一覧」(RakutenHoldingsAdapter)
        保有している国内株式を、基準日の「入庫」(数量と平均取得価額)として取り込む。
        CSV より前からの保有(期首の保有)を入れるのに使う。米国株・投資信託などは Phase 3 で対応する
    楽天証券「取引履歴(国内株式)」(RakutenTradesAdapter)
        現物の買付・売付・入庫・出庫を取り込む。信用取引は対象外。
        ※ 列名は想定したもので、実際の CSV ではまだ確かめていない(列名が全部そろったときだけ使う)

使い方(他のプログラムから):
    adapter, rows = read_csv(path, ImportContext(base_date="2026-11-18", make_instrument=...))
    classify(rows, store, ledger)
    check_problems(rows, store)
    count = commit(rows, store)
"""

import csv
import io
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from core_asset import EPSILON, AssetStore, Instrument, Ledger, Trade, build_ledger, changed_problems

# 取り込みの状態 -> (表示名、既定で取り込むか、取り込むかどうかを利用者が切り替えられるか)
STATUSES = {
    "new": ("新規", True, True),
    "maybe_duplicate": ("手入力と重複?", False, True),
    "exists": ("保有あり", False, True),
    "duplicate": ("取り込み済み", False, False),
    "skip": ("対象外", False, False),
    "error": ("エラー", False, False),
}

# 証券会社の口座区分の表記 -> このプログラムの口座区分
ACCOUNT_NAMES = {
    "特定": "特定",
    "一般": "一般",
    "NISA成長投資枠": "NISA成長",
    "NISA成長": "NISA成長",
    "NISAつみたて投資枠": "NISAつみたて",
    "NISAつみたて": "NISAつみたて",
    "旧NISA": "旧NISA",
    "NISA": "旧NISA",
    "つみたてNISA": "旧NISA",
    "旧つみたてNISA": "旧NISA",
}


@dataclass
class ImportContext:
    """取り込みに必要な、CSV の外から渡す情報。"""
    base_date: str                               # 保有の CSV を入庫として入れる日 "YYYY-MM-DD"
    make_instrument: Callable[[str, str], Instrument]  # (コード, CSV の銘柄名) -> 商品


@dataclass
class ImportRow:
    """CSV の 1 行と、その変換結果。"""
    line: int                       # CSV の行番号(1 始まり)
    values: dict[str, str]          # 元の行(列名 -> 値)
    trade: Trade | None = None      # 変換できた取引(id は 0。確定のときに採番する)
    instrument: Instrument | None = None
    status: str = "new"             # STATUSES のどれか
    message: str = ""               # 状態の理由など
    selected: bool = True           # 取り込むか

    def set_status(self, status: str, message: str = "") -> None:
        self.status, self.message = status, message
        self.selected = STATUSES[status][1]

    @property
    def toggleable(self) -> bool:
        return STATUSES[self.status][2]


def clean(text: str) -> str:
    """セルの値の前後の空白を除き、全角英数字を半角にそろえる。"""
    return unicodedata.normalize("NFKC", text or "").strip()


def to_number(text: str) -> float | None:
    """"3,289.85" や "+1,200" を数にする。"-" や空は None。"""
    text = clean(text).replace(",", "").replace("+", "")
    if text in ("", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_date(text: str) -> str | None:
    """"2026/11/18" などを "YYYY-MM-DD" にする。"""
    text = clean(text)
    for pattern in ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


class CsvAdapter(ABC):
    """証券会社の CSV 1 形式ぶんの変換。"""
    key: str              # "rakuten_holdings" など。Trade.source は "csv:" + key
    label: str            # 表示名「楽天証券(保有商品一覧)」
    broker: str           # 取引に記録する証券会社名
    kind: str             # "holdings"(保有の一覧) / "trades"(取引の履歴)
    encodings = ("cp932", "utf-8-sig")
    required: tuple[str, ...] = ()  # 見出しの行にそろっているべき列名

    def find_header(self, table: list[list[str]]) -> int | None:
        """表の中から見出しの行を探して、その行番号(0 始まり)を返す。見つからなければ None。"""
        for i, cells in enumerate(table):
            names = {clean(c) for c in cells}
            if all(name in names for name in self.required):
                return i
        return None

    def data_rows(self, table: list[list[str]], header: int) -> list[tuple[int, dict[str, str]]]:
        """見出しの次の行から、空の行が来るまでを (行番号、列名 -> 値) にする。"""
        names = [clean(c) for c in table[header]]
        rows = []
        for i in range(header + 1, len(table)):
            cells = table[i]
            if not any(clean(c) for c in cells):
                break
            rows.append((i + 1, {name: (cells[j] if j < len(cells) else "") for j, name in enumerate(names)}))
        return rows

    @abstractmethod
    def convert(self, line: int, values: dict[str, str], context: ImportContext) -> ImportRow: ...

    def import_key(self, *parts) -> str:
        return "|".join([self.key, *(str(p) for p in parts)])


class RakutenHoldingsAdapter(CsvAdapter):
    """楽天証券「保有商品一覧」CSV。国内株式の保有を、基準日の入庫として取り込む。"""
    key = "rakuten_holdings"
    label = "楽天証券(保有商品一覧)"
    broker = "楽天証券"
    kind = "holdings"
    required = ("種別", "銘柄コード・ティッカー", "銘柄", "口座", "保有数量", "平均取得価額")

    def convert(self, line: int, values: dict[str, str], context: ImportContext) -> ImportRow:
        row = ImportRow(line, values)
        kind = clean(values.get("種別", ""))
        if "預り金" in kind:
            row.set_status("skip", "預り金(現金)は管理しません")
            return row
        if kind != "国内株式":
            row.set_status("skip", f"{kind or '不明'}は未対応です(Phase 3 で対応)")
            return row
        code = clean(values.get("銘柄コード・ティッカー", "")).upper()
        account = ACCOUNT_NAMES.get(clean(values.get("口座", "")))
        quantity = to_number(values.get("保有数量", ""))
        price = to_number(values.get("平均取得価額", ""))
        if not code:
            row.set_status("error", "銘柄コードがありません")
        elif account is None:
            row.set_status("error", f"口座「{clean(values.get('口座', ''))}」が分かりません")
        elif not quantity or quantity <= 0 or price is None or price < 0:
            row.set_status("error", "保有数量または平均取得価額が読めません")
        if row.status == "error":
            return row
        row.instrument = context.make_instrument(code, clean(values.get("銘柄", "")))
        row.trade = Trade(
            id=0, date=context.base_date, instrument_id=row.instrument.id, broker=self.broker, account=account,
            action="transfer_in", quantity=quantity, price=price,
            memo="保有商品一覧から取り込み", source="csv:" + self.key,
            import_key=self.import_key(context.base_date, code, account),
        )
        return row


class RakutenTradesAdapter(CsvAdapter):
    """楽天証券「取引履歴(国内株式)」CSV。

    ※ 列名は想定したもので、実際の CSV ではまだ確かめていない。required の列が全部そろったときだけ使う。
    """
    key = "rakuten_trades"
    label = "楽天証券(取引履歴 国内株式)"
    broker = "楽天証券"
    kind = "trades"
    required = ("約定日", "銘柄コード", "銘柄名", "口座区分", "取引区分", "売買区分", "数量［株］", "単価［円］")

    SIDES = {"買付": "buy", "売付": "sell", "入庫": "transfer_in", "出庫": "transfer_out"}

    def convert(self, line: int, values: dict[str, str], context: ImportContext) -> ImportRow:
        row = ImportRow(line, values)
        trade_type = clean(values.get("取引区分", ""))
        side = self.SIDES.get(clean(values.get("売買区分", "")))
        if trade_type and trade_type != "現物" and side not in ("transfer_in", "transfer_out"):
            row.set_status("skip", f"{trade_type}は対象外です(現物だけを取り込みます)")
            return row
        date = to_date(values.get("約定日", ""))
        code = clean(values.get("銘柄コード", "")).upper()
        account = ACCOUNT_NAMES.get(clean(values.get("口座区分", "")))
        quantity = to_number(values.get("数量［株］", ""))
        price = to_number(values.get("単価［円］", ""))
        # 手数料は「手数料」と「諸費用」の合計。「税金等」(源泉徴収税)は税を反映しない方針なので使わない
        fee = (to_number(values.get("手数料［円］", "")) or 0) + (to_number(values.get("諸費用［円］", "")) or 0)
        if side is None:
            row.set_status("skip", f"売買区分「{clean(values.get('売買区分', ''))}」は対象外です")
        elif date is None:
            row.set_status("error", "約定日が読めません")
        elif not code:
            row.set_status("error", "銘柄コードがありません")
        elif account is None:
            row.set_status("error", f"口座区分「{clean(values.get('口座区分', ''))}」が分かりません")
        elif not quantity or quantity <= 0:
            row.set_status("error", "数量が読めません")
        elif price is None and side != "transfer_out":
            row.set_status("error", "単価が読めません(入庫は取得単価を手入力してください)")
        if row.status != "new":
            return row
        amount = to_number(values.get("受渡金額［円］", ""))
        tax = to_number(values.get("税金等［円］", "")) or 0
        if amount is not None and side in ("buy", "sell"):
            expected = quantity * price + (fee + tax if side == "buy" else -fee - tax)
            if abs(abs(amount) - expected) > 1:
                row.set_status("error", f"受渡金額({amount:,.0f})が数量×単価と手数料に合いません")
                return row
        row.instrument = context.make_instrument(code, clean(values.get("銘柄名", "")))
        row.trade = Trade(
            id=0, date=date, instrument_id=row.instrument.id, broker=self.broker, account=account, action=side,
            quantity=quantity, price=price or 0.0, fee=fee if side in ("buy", "sell") else 0.0,
            source="csv:" + self.key,
        )
        return row


ADAPTERS: list[CsvAdapter] = [RakutenHoldingsAdapter(), RakutenTradesAdapter()]


def read_table(path: Path, encodings: tuple[str, ...]) -> list[list[str]]:
    """CSV を表(行のリスト)として読む。文字コードは encodings の順に試す。"""
    data = path.read_bytes()
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return list(csv.reader(io.StringIO(text)))
    raise ValueError("CSV の文字コードが分かりません")


def read_csv(path: Path, context: ImportContext) -> tuple[CsvAdapter, list[ImportRow]]:
    """CSV を読み、合うアダプタで変換する。合うアダプタがなければ ValueError。"""
    tables: dict[tuple[str, ...], list[list[str]]] = {}
    for adapter in ADAPTERS:
        if adapter.encodings not in tables:
            try:
                tables[adapter.encodings] = read_table(path, adapter.encodings)
            except ValueError:
                tables[adapter.encodings] = []
        table = tables[adapter.encodings]
        header = adapter.find_header(table)
        if header is None:
            continue
        rows = [adapter.convert(line, values, context) for line, values in adapter.data_rows(table, header)]
        number_duplicates(adapter, rows)
        return adapter, rows
    raise ValueError("対応している形式の CSV ではありません。\n対応している形式: "
                     + "、".join(a.label for a in ADAPTERS))


def number_duplicates(adapter: CsvAdapter, rows: list[ImportRow]) -> None:
    """取引の履歴では、同じ内容の取引が同じ CSV に何件あるかを import_key に入れる(同じ日に同じ注文を 2 回など)。"""
    if adapter.kind != "trades":
        return
    seen: dict[str, int] = {}
    for row in rows:
        t = row.trade
        if t is None:
            continue
        base = adapter.import_key(t.date, t.instrument_id, t.account, t.action, t.quantity, t.price)
        seen[base] = seen.get(base, 0) + 1
        t.import_key = f"{base}|{seen[base]}"


def classify(rows: list[ImportRow], store: AssetStore, ledger: Ledger) -> None:
    """取り込み済み・手入力との重複・保有ありを判定する。"""
    keys = {t.import_key for t in store.trades if t.import_key}
    manual = {
        (t.date, t.instrument_id, t.broker, t.account, t.action, t.quantity, t.price)
        for t in store.trades if t.source == "manual"
    }
    for row in rows:
        t = row.trade
        if t is None or row.status != "new":
            continue
        if t.import_key in keys:
            row.set_status("duplicate", "取り込み済みです")
        elif (t.date, t.instrument_id, t.broker, t.account, t.action, t.quantity, t.price) in manual:
            row.set_status("maybe_duplicate", "手入力の取引に同じ内容があります")
        elif t.action == "transfer_in" and t.source == "csv:rakuten_holdings":
            holding = ledger.holdings.get(t.key)
            if holding is not None:
                same = abs(holding.quantity - t.quantity) < EPSILON
                row.set_status(
                    "duplicate" if same else "exists",
                    "すでに同じ数量を保有しています" if same
                    else f"すでに {holding.quantity:,.10g} 株を保有しています(取り込むと足されます)",
                )


def check_problems(rows: list[ImportRow], store: AssetStore) -> None:
    """取り込む行を全部入れて集計し直し、数量が足りなくなる行をエラーにする。

    エラーになった (銘柄 × 証券会社 × 口座) の行はまとめて外す(残りの取引だけでは集計がずれるため)。
    エラーの行を外すと別の行が問題になることもあるので、問題がなくなるまでくり返す。
    """
    while True:
        chosen = [r for r in rows if r.selected and r.trade is not None]
        trades = []
        for n, row in enumerate(sorted(chosen, key=import_order)):
            trade = replace(row.trade, id=-(n + 1))  # 仮の id(負の数で既存と重ねない)
            trades.append((trade, row))
        instruments = {**store.instruments, **{r.instrument.id: r.instrument for r in chosen}}
        problems = changed_problems(store.trades, [*store.trades, *(t for t, _ in trades)], instruments)
        if not problems:
            return
        ledger = build_ledger([*store.trades, *(t for t, _ in trades)], instruments)
        bad_ids = {p.trade_id for p in ledger.problems}
        by_id = {t.id: (t, row) for t, row in trades}
        bad_keys = {by_id[i][0].key: by_id[i][1] for i in bad_ids if i in by_id}
        if not bad_keys:
            return  # 取り込む行ではなく、もとからある取引の問題
        messages = {p.trade_id: p.message for p in ledger.problems}
        for t, row in trades:
            if t.key in bad_keys:
                reason = messages.get(t.id)
                row.set_status("error", reason or "同じ銘柄・口座の取引に、集計できない行があります")


def import_order(row: ImportRow) -> tuple:
    """取り込む順番。同じ日の中は 入庫 -> 買い -> 売り -> 出庫 の順にする(同じ日の売買で数量が足りなくならないように)。"""
    order = {"transfer_in": 0, "buy": 1, "split": 2, "sell": 3, "transfer_out": 4}
    return (row.trade.date, order.get(row.trade.action, 9), row.line)


def commit(rows: list[ImportRow], store: AssetStore, broker: str) -> int:
    """選ばれた行を取り込む(採番して store に入れる。保存は呼び出し側で行う)。入れた件数を返す。"""
    chosen = sorted((r for r in rows if r.selected and r.trade is not None), key=import_order)
    if broker not in store.brokers:
        store.brokers.append(broker)
    next_id = store.next_id()
    for n, row in enumerate(chosen):
        store.ensure_instrument(row.instrument)
        store.put_trade(replace(row.trade, id=next_id + n))
    return len(chosen)


def status_counts(rows: list[ImportRow]) -> dict[str, int]:
    """状態ごとの行数。"""
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row.status] += 1
    return counts

