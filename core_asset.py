"""資産管理のデータ(商品・取引・保有)と、その保存・集計(GUI を含まない部分)。

設計は fina_asset_design.md を参照。
中心に置くのは「取引」の記録で、保有と実現損益は取引の全件から毎回集計し直す(保存しない)。
平均取得単価は移動平均法で計算する。税は反映しない(実現損益は税引前)。

保存先はルートディレクトリの asset.json。読み書きは AssetStore だけが行う
(将来 SQLite などへ移すときは、このクラスだけを直せばよいようにする)。

使い方(他のプログラムから):
    store = AssetStore()
    store.load()
    ledger = build_ledger(store.trades, store.instruments)
    for holding in ledger.holdings.values():
        print(holding.key, holding.quantity, holding.average_price)
"""

import json
import os
from collections.abc import Callable
from dataclasses import MISSING, asdict, dataclass, field, fields
from enum import StrEnum
from pathlib import Path

from core_gather import BASE_DIR

ASSET_PATH = BASE_DIR / "asset.json"
FORMAT_VERSION = 1

# 口座区分(保有ごとに設定する)
ACCOUNT_TYPES = ["特定", "一般", "NISA成長", "NISAつみたて", "旧NISA"]

# 売買区分 -> 表示名
ACTIONS = {
    "buy": "買い", "sell": "売り", "split": "分割",
    "transfer_in": "入庫",    # 期首の保有・移管で受け入れた分。単価はそのときの取得単価。実現損益なし
    "transfer_out": "出庫",   # 移管で出した分。平均単価で原価を取り崩す。実現損益なし
    "swap": "スワップ",
}
TRANSFERS = ("transfer_in", "transfer_out")

EPSILON = 1e-9  # 数量が 0 になったとみなす幅(float の誤差を吸収する)


class AssetClass(StrEnum):
    """資産クラス。"""
    JP_STOCK = "jp_stock"  # 日本株現物(ETF・REIT を含む)   Phase 1
    US_STOCK = "us_stock"  # 米国株                          Phase 3
    FUND = "fund"          # 投資信託                        Phase 3
    FX = "fx"              # FX                              Phase 4
    OPTION = "option"      # オプション(日経225・米国)       Phase 4


@dataclass(frozen=True)
class AssetClassRule:
    """資産クラスごとの決まり。"""
    label: str          # 表示名
    allow_short: bool   # 売り建て(数量がマイナス)を許すか
    unit_label: str     # 数量の単位


ASSET_CLASS_RULES = {
    AssetClass.JP_STOCK: AssetClassRule("日本株", False, "株"),
    AssetClass.US_STOCK: AssetClassRule("米国株", False, "株"),
    AssetClass.FUND: AssetClassRule("投資信託", False, "口"),
    AssetClass.FX: AssetClassRule("FX", True, "通貨"),
    AssetClass.OPTION: AssetClassRule("オプション", True, "枚"),
}


# ---- データの型 ----

@dataclass(frozen=True)
class Instrument:
    """商品マスタの 1 件。"""
    id: str                 # "JP:7203" など。取引からはこの ID で参照する
    asset_class: AssetClass
    code: str               # 銘柄コード・ティッカー・通貨ペアなど
    name: str
    currency: str = "JPY"   # 価格の通貨
    market: str = ""        # 市場(プライム、NASDAQ など)
    sector: str = ""        # セクター(日本株は銘柄一覧の 33 業種区分)
    # --- オプション用 ---
    underlying: str = ""    # 原資産(N225、AAPL など)
    right: str = ""         # "call" / "put"
    strike: float | None = None
    expiry: str = ""        # 限月 "2026-12"
    multiplier: float = 1   # 倍率(日経225オプション 1000、米国オプション 100)
    # --- FX 用 ---
    pair: str = ""          # "USDJPY" など

    @property
    def rule(self) -> AssetClassRule:
        return ASSET_CLASS_RULES[self.asset_class]


def jp_stock_id(code: str) -> str:
    """日本株の商品 ID。"""
    return f"JP:{code}"


def jp_stock_instrument(code: str, listed=None, known: dict[str, "Instrument"] | None = None,
                        name: str = "") -> "Instrument":
    """日本株の商品を作る。

    listed(銘柄一覧。コードが行の名前の DataFrame。core_stocks.load_listed() の戻り値)に載っていれば、
    銘柄名・市場・33 業種をそこから写す。載っていなければ、登録済みの商品(known)、
    それもなければ name を銘柄名にする(上場廃止の銘柄など)。
    """
    if listed is not None and code in listed.index:
        row = listed.loc[code]

        def value(column):
            v = row.get(column, "")
            return v if isinstance(v, str) and v != "-" else ""

        return Instrument(
            jp_stock_id(code), AssetClass.JP_STOCK, code, value("銘柄名"),
            market=value("市場・商品区分").split("（")[0], sector=value("33業種区分"),
        )
    found = (known or {}).get(jp_stock_id(code))
    return found or Instrument(jp_stock_id(code), AssetClass.JP_STOCK, code, name)


@dataclass
class Trade:
    """取引 1 件(データの中心)。"""
    id: int                 # 通し番号(作成順。同じ日の取引の順番にも使う)
    date: str               # 約定日 "YYYY-MM-DD"
    instrument_id: str
    broker: str             # 証券会社
    account: str            # 口座区分(ACCOUNT_TYPES のどれか)
    action: str             # "buy" / "sell" / "split" / "swap"
    quantity: float         # 数量。常に正の数(split では使わない)
    price: float            # 約定単価(商品の通貨)。split では分割比率(1株→2株なら 2)
    fee: float = 0          # 手数料(税込み・円)
    time: str = ""          # 約定時刻 "HH:MM"(任意)
    fx_rate: float | None = None  # 約定時の為替レート(外貨建て商品のみ、Phase 3)
    memo: str = ""
    source: str = "manual"  # "manual" / "csv:rakuten" など
    import_key: str = ""    # CSV 取り込みの重複判定用(Phase 2)

    @property
    def key(self) -> "HoldingKey":
        return (self.instrument_id, self.broker, self.account)

    @property
    def order(self) -> tuple:
        """処理する順番(約定日、時刻、入力順)。"""
        return (self.date, self.time, self.id)


@dataclass(frozen=True)
class Snapshot:
    """資産推移の記録 1 日分。"""
    date: str             # 記録した日(アプリを起動した日)
    price_date: str       # 評価に使った価格の日付
    value: float          # 評価額の合計(円)
    cost: float           # 取得原価の合計(円)
    unrealized: float     # 含み損益の合計(円)
    realized_total: float # 実現損益の累計(円)
    by_class: dict[str, float] = field(default_factory=dict)  # 資産クラス -> 評価額
    # "証券会社|口座区分" -> [評価額, 取得原価](証券会社・口座区分で絞り込んだ推移を描くため)
    by_group: dict[str, list[float]] = field(default_factory=dict)


# ---- 保有と実現損益(取引から集計する。保存しない) ----

HoldingKey = tuple[str, str, str]  # (instrument_id, broker, account)


@dataclass
class Holding:
    """保有 1 件((商品 × 証券会社 × 口座区分)ごと)。"""
    key: HoldingKey
    quantity: float = 0     # 正ならロング、負ならショート(FX・オプションのみ)
    cost: float = 0         # 取得原価の合計(商品の通貨。買い手数料を含む)。ショートでは建てたときの受取額
    multiplier: float = 1

    @property
    def instrument_id(self) -> str:
        return self.key[0]

    @property
    def broker(self) -> str:
        return self.key[1]

    @property
    def account(self) -> str:
        return self.key[2]

    @property
    def average_price(self) -> float:
        """平均取得単価(移動平均法)。"""
        size = abs(self.quantity) * self.multiplier
        return self.cost / size if size > EPSILON else 0.0


@dataclass(frozen=True)
class Realized:
    """決済 1 件の実現損益。"""
    trade_id: int
    date: str
    key: HoldingKey
    quantity: float
    proceeds: float   # 受け取った額(ロングの決済は売却代金 − 売り手数料、ショートの決済は建てたときの受取額)
    cost: float       # 支払った額(ロングの決済は平均単価 × 数量、ショートの決済は買い戻し代金 + 手数料)

    @property
    def profit(self) -> float:
        """損益(税引前)。"""
        return self.proceeds - self.cost


@dataclass(frozen=True)
class Problem:
    """集計できなかった取引と、その理由。"""
    trade_id: int
    message: str


@dataclass
class Ledger:
    """取引を集計した結果。"""
    holdings: dict[HoldingKey, Holding] = field(default_factory=dict)
    realized: list[Realized] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


def build_ledger(
    trades: list[Trade], instruments: dict[str, Instrument],
    on_date: Callable[[str, dict[HoldingKey, Holding]], None] | None = None,
) -> Ledger:
    """取引を (約定日、時刻、入力順) の順に処理して、保有と実現損益を集計する(移動平均法)。

    保有数量を超える売り(売り建てを許さない資産クラス)は、超えた分を無視して problems に記録する。
    数量が 0 になった保有は holdings から除く。
    on_date を渡すと、1 日分の取引を処理し終えるたびに (約定日、その時点の保有) を渡して呼ぶ
    (保有は呼び出しのあとも変わるので、必要なら写しを取ること)。
    """
    ledger = Ledger()
    holdings: dict[HoldingKey, Holding] = {}
    ordered = sorted(trades, key=lambda t: t.order)
    for n, trade in enumerate(ordered):
        instrument = instruments.get(trade.instrument_id)
        if instrument is None:
            ledger.problems.append(Problem(trade.id, f"商品 {trade.instrument_id} が商品マスタにありません。"))
        else:
            holding = holdings.setdefault(trade.key, Holding(trade.key, multiplier=instrument.multiplier))
            if trade.action == "split":
                _apply_split(ledger, holding, trade)
            elif trade.action in TRANSFERS:
                _apply_transfer(ledger, holding, trade)
            elif trade.action in ("buy", "sell"):
                _apply_trade(ledger, holding, trade, instrument.rule.allow_short)
            # swap(FX のスワップ)は Phase 4 で扱う
            if abs(holding.quantity) < EPSILON:
                holding.quantity, holding.cost = 0, 0
        if on_date is not None and (n + 1 == len(ordered) or ordered[n + 1].date != trade.date):
            on_date(trade.date, holdings)
    ledger.holdings = {key: h for key, h in holdings.items() if h.quantity != 0}
    return ledger


def _apply_split(ledger: Ledger, holding: Holding, trade: Trade) -> None:
    """株式分割。数量を比率倍にする(原価はそのままなので、平均単価は ÷ 比率になる)。"""
    if trade.price <= 0:
        ledger.problems.append(Problem(trade.id, "分割比率が 0 以下です。"))
        return
    if holding.quantity == 0:
        ledger.problems.append(Problem(trade.id, "分割の時点で、この銘柄を保有していません。"))
        return
    holding.quantity *= trade.price


def _apply_transfer(ledger: Ledger, holding: Holding, trade: Trade) -> None:
    """入庫・出庫。保有の場所が変わるだけなので、実現損益は出さない(ロングの保有だけを扱う)。"""
    if holding.quantity < 0:
        ledger.problems.append(Problem(trade.id, "売り建ての保有には、入庫・出庫を使えません。"))
        return
    if trade.action == "transfer_in":
        holding.cost += trade.quantity * trade.price * holding.multiplier
        holding.quantity += trade.quantity
        return
    if trade.quantity > holding.quantity + EPSILON:
        ledger.problems.append(Problem(
            trade.id, f"保有数量({holding.quantity:,.10g})を超える出庫です。",
        ))
        return
    holding.cost -= holding.cost * trade.quantity / holding.quantity
    holding.quantity -= trade.quantity


def _apply_trade(ledger: Ledger, holding: Holding, trade: Trade, allow_short: bool) -> None:
    """買い・売り 1 件を保有に反映する。"""
    direction = 1 if trade.action == "buy" else -1
    remaining, fee = trade.quantity, trade.fee
    size = holding.multiplier

    # 反対向きの保有があれば、まず平均単価で決済する
    if holding.quantity * direction < 0:
        closing = min(remaining, abs(holding.quantity))
        closing_fee = fee * closing / remaining
        basis = holding.cost * closing / abs(holding.quantity)
        amount = closing * trade.price * size
        if direction < 0:  # ロングの売り決済
            proceeds, cost = amount - closing_fee, basis
        else:              # ショートの買い戻し
            proceeds, cost = basis, amount + closing_fee
        ledger.realized.append(Realized(trade.id, trade.date, holding.key, closing, proceeds, cost))
        holding.cost -= basis
        holding.quantity += direction * closing
        remaining -= closing
        fee -= closing_fee
        if remaining < EPSILON:
            return
        if not allow_short:
            ledger.problems.append(Problem(
                trade.id, f"保有数量({closing:,.10g})を超える売りです(超えた {remaining:,.10g} は無視しました)。",
            ))
            return
    elif direction < 0 and not allow_short:
        ledger.problems.append(Problem(trade.id, "この銘柄を保有していない時点の売りです。"))
        return

    # 同じ向きの新規・追加。ロングは支払額(手数料を含む)、ショートは受取額(手数料を引く)を原価にする
    amount = remaining * trade.price * size
    holding.cost += amount + fee if direction > 0 else amount - fee
    holding.quantity += direction * remaining


def validate_trade(trade: Trade, trades: list[Trade], instruments: dict[str, Instrument]) -> list[str]:
    """取引の入力内容を確かめる。問題があれば、その理由の一覧を返す(なければ空)。

    trades は今の取引の全件。同じ id の取引があれば置き換えたものとして、全件で集計し直して確かめる
    (過去の日付に売りを入れて、途中で数量がマイナスになる場合も見つける)。
    """
    errors = []
    if trade.action not in ACTIONS:
        errors.append("売買区分が正しくありません。")
    if trade.account not in ACCOUNT_TYPES:
        errors.append("口座区分を選んでください。")
    if not trade.broker:
        errors.append("証券会社を選んでください。")
    if trade.action == "split":
        if trade.price <= 0:
            errors.append("分割比率は 0 より大きい数にしてください。")
    else:
        if trade.quantity <= 0:
            errors.append("数量は 0 より大きい数にしてください。")
        if trade.price < 0:
            errors.append("単価は 0 以上にしてください。")
        if trade.fee < 0:
            errors.append("手数料は 0 以上にしてください。")
    if errors:
        return errors
    others = [t for t in trades if t.id != trade.id]
    return changed_problems(others, [*others, trade], instruments)


def changed_problems(before: list[Trade], after: list[Trade], instruments: dict[str, Instrument]) -> list[str]:
    """取引を before から after に変えたときに、新しく生まれる集計の問題を返す。

    もとから問題のある取引(手で書き換えたファイルなど)があっても、他の取引を直せるようにする。
    """
    old = set(build_ledger(before, instruments).problems)
    by_id = {t.id: t for t in after}
    messages = []
    for problem in build_ledger(after, instruments).problems:
        if problem in old:
            continue
        t = by_id.get(problem.trade_id)
        where = f"{t.date} の{ACTIONS.get(t.action, t.action)}" if t else f"取引 {problem.trade_id}"
        messages.append(f"{where}: {problem.message}")
    return messages


# ---- 保存(asset.json) ----

def _to_dict(obj) -> dict:
    """データクラスを辞書にする。既定値のままの項目は書き出さない(ファイルを読みやすくする)。"""
    result = {}
    for f in fields(obj):
        value = getattr(obj, f.name)
        if f.default is not MISSING and value == f.default:
            continue
        if isinstance(value, float) and value.is_integer():
            value = int(value)  # 100.0 -> 100(ファイルを読みやすくする)
        result[f.name] = value
    return result


def _number(value, default=None) -> float | None:
    """数として読める値なら float に、読めなければ default にする(bool は数とみなさない)。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value)


def _text(value, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def parse_instrument(instrument_id, saved) -> Instrument | None:
    """保存されていた商品を Instrument に直す。形が違えば None。"""
    if not isinstance(instrument_id, str) or not isinstance(saved, dict):
        return None
    try:
        asset_class = AssetClass(saved.get("asset_class"))
    except ValueError:
        return None
    code = _text(saved.get("code"))
    if not code:
        return None
    multiplier = _number(saved.get("multiplier"), 1.0)
    return Instrument(
        id=instrument_id,
        asset_class=asset_class,
        code=code,
        name=_text(saved.get("name")),
        currency=_text(saved.get("currency"), "JPY") or "JPY",
        market=_text(saved.get("market")),
        sector=_text(saved.get("sector")),
        underlying=_text(saved.get("underlying")),
        right=_text(saved.get("right")),
        strike=_number(saved.get("strike")),
        expiry=_text(saved.get("expiry")),
        multiplier=multiplier if multiplier > 0 else 1.0,
        pair=_text(saved.get("pair")),
    )


def parse_trade(saved) -> Trade | None:
    """保存されていた取引を Trade に直す。形が違えば None。"""
    if not isinstance(saved, dict):
        return None
    trade_id = saved.get("id")
    quantity, price = _number(saved.get("quantity"), 0.0), _number(saved.get("price"))
    date = _text(saved.get("date"))
    if not isinstance(trade_id, int) or isinstance(trade_id, bool) or price is None or len(date) != 10:
        return None
    action = _text(saved.get("action"))
    if action not in ACTIONS:
        return None
    return Trade(
        id=trade_id,
        date=date,
        instrument_id=_text(saved.get("instrument_id")),
        broker=_text(saved.get("broker")),
        account=_text(saved.get("account")),
        action=action,
        quantity=quantity,
        price=price,
        fee=_number(saved.get("fee"), 0.0),
        time=_text(saved.get("time")),
        fx_rate=_number(saved.get("fx_rate")),
        memo=_text(saved.get("memo")),
        source=_text(saved.get("source"), "manual") or "manual",
        import_key=_text(saved.get("import_key")),
    )


def parse_snapshot(saved) -> Snapshot | None:
    """保存されていたスナップショットを Snapshot に直す。形が違えば None。"""
    if not isinstance(saved, dict) or len(_text(saved.get("date"))) != 10:
        return None
    by_class = saved.get("by_class")
    by_class = {k: float(v) for k, v in by_class.items() if _number(v) is not None} if isinstance(by_class, dict) else {}
    by_group = {}
    for k, v in (saved.get("by_group") or {}).items() if isinstance(saved.get("by_group"), dict) else []:
        if isinstance(v, list) and len(v) == 2 and all(_number(x) is not None for x in v):
            by_group[k] = [float(v[0]), float(v[1])]
    return Snapshot(
        date=_text(saved.get("date")),
        price_date=_text(saved.get("price_date")),
        value=_number(saved.get("value"), 0.0),
        cost=_number(saved.get("cost"), 0.0),
        unrealized=_number(saved.get("unrealized"), 0.0),
        realized_total=_number(saved.get("realized_total"), 0.0),
        by_class=by_class,
        by_group=by_group,
    )


class AssetStore:
    """asset.json の読み書き。商品マスタ・取引・証券会社の一覧・スナップショット・画面の状態を持つ。"""

    def __init__(self, path: Path = ASSET_PATH):
        self.path = path
        self.instruments: dict[str, Instrument] = {}
        self.trades: list[Trade] = []
        self.brokers: list[str] = []
        self.snapshots: list[Snapshot] = []
        self.ui: dict = {}

    def load(self) -> None:
        """ファイルを読み込む。ファイルがなければ空のまま。

        手で書き換えたファイルでも止まらないよう、形の違う要素は飛ばす。
        ファイル全体が読めないとき(JSON として壊れているなど)は ValueError。
        """
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("ファイルの形式が違います")
        saved = data.get("instruments")
        self.instruments = {}
        for instrument_id, value in (saved.items() if isinstance(saved, dict) else []):
            instrument = parse_instrument(instrument_id, value)
            if instrument is not None:
                self.instruments[instrument.id] = instrument
        self.trades = []
        ids = set()
        for value in data.get("trades") if isinstance(data.get("trades"), list) else []:
            trade = parse_trade(value)
            if trade is not None and trade.id not in ids:
                ids.add(trade.id)
                self.trades.append(trade)
        brokers = data.get("brokers")
        self.brokers = list(dict.fromkeys(
            b.strip() for b in (brokers if isinstance(brokers, list) else []) if isinstance(b, str) and b.strip()
        ))
        # 取引に使われているのに一覧にない証券会社も、一覧に入れる
        for trade in self.trades:
            if trade.broker and trade.broker not in self.brokers:
                self.brokers.append(trade.broker)
        snapshots = data.get("snapshots")
        parsed = [parse_snapshot(s) for s in (snapshots if isinstance(snapshots, list) else [])]
        self.snapshots = sorted({s.date: s for s in parsed if s is not None}.values(), key=lambda s: s.date)
        self.ui = data.get("ui") if isinstance(data.get("ui"), dict) else {}

    def save(self) -> None:
        """ファイルに保存する。書き込みの途中で止まっても元のファイルが壊れないよう、別のファイルに書いてから置き換える。"""
        data = {
            "version": FORMAT_VERSION,
            "brokers": self.brokers,
            "instruments": {
                i.id: {k: v for k, v in _to_dict(i).items() if k != "id"} for i in self.instruments.values()
            },
            "trades": [_to_dict(t) for t in sorted(self.trades, key=lambda t: t.order)],
            "snapshots": [asdict(s) for s in self.snapshots],
            "ui": self.ui,
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)

    # ---- 取引 ----

    def next_id(self) -> int:
        """新しい取引の通し番号。"""
        return max((t.id for t in self.trades), default=0) + 1

    def trade(self, trade_id: int) -> Trade | None:
        return next((t for t in self.trades if t.id == trade_id), None)

    def put_trade(self, trade: Trade) -> None:
        """取引を追加する。同じ id の取引があれば置き換える。"""
        self.trades = [t for t in self.trades if t.id != trade.id] + [trade]

    def delete_trade(self, trade_id: int) -> None:
        self.trades = [t for t in self.trades if t.id != trade_id]

    def ensure_instrument(self, instrument: Instrument) -> None:
        """商品マスタに商品を登録する(すでにあれば置き換える)。"""
        self.instruments[instrument.id] = instrument

    # ---- 証券会社 ----

    def rename_broker(self, old: str, new: str) -> None:
        """証券会社の名前を変える。取引に記録された名前も変える。"""
        self.brokers = [new if b == old else b for b in self.brokers]
        for trade in self.trades:
            if trade.broker == old:
                trade.broker = new

    def broker_in_use(self, name: str) -> bool:
        return any(t.broker == name for t in self.trades)


def _self_test() -> None:
    """移動平均法の集計を確かめる(python core_asset.py で実行)。"""
    jp = Instrument("JP:7203", AssetClass.JP_STOCK, "7203", "トヨタ自動車")
    instruments = {jp.id: jp}

    def t(i, date, action, qty, price, fee=0):
        return Trade(i, date, jp.id, "楽天証券", "特定", action, qty, price, fee)

    # 買い増し -> 平均単価、一部売却 -> 実現損益
    trades = [t(1, "2026-01-05", "buy", 100, 1000, 100), t(2, "2026-02-01", "buy", 100, 1200)]
    ledger = build_ledger(trades, instruments)
    h = ledger.holdings[trades[0].key]
    assert h.quantity == 200 and abs(h.average_price - 1100.5) < 1e-9, h
    trades.append(t(3, "2026-03-01", "sell", 50, 1300, 50))
    ledger = build_ledger(trades, instruments)
    r = ledger.realized[0]
    assert abs(r.profit - (50 * 1300 - 50 - 50 * 1100.5)) < 1e-9, r
    assert abs(ledger.holdings[trades[0].key].average_price - 1100.5) < 1e-9

    # 分割(1 株 -> 2 株)で数量 2 倍、平均単価 1/2
    trades.append(t(4, "2026-04-01", "split", 0, 2))
    h = build_ledger(trades, instruments).holdings[trades[0].key]
    assert h.quantity == 300 and abs(h.average_price - 550.25) < 1e-9, h

    # 全売却で保有から消える
    trades.append(t(5, "2026-05-01", "sell", 300, 600))
    ledger = build_ledger(trades, instruments)
    assert not ledger.holdings and len(ledger.realized) == 2 and not ledger.problems

    # 過去の日付に売りを入れて途中で数量が足りなくなる場合はエラー
    errors = validate_trade(t(6, "2026-01-04", "sell", 10, 1000), trades, instruments)
    assert errors, errors
    # 同じ日の買い -> 売りは入力順で処理する
    assert not validate_trade(t(6, "2026-05-01", "buy", 10, 600), trades, instruments)

    # 入庫(期首の保有)のあとの売り。移管(出庫 -> 別の証券会社に入庫)は実現損益を出さない
    def u(i, date, action, qty, price, broker="楽天証券"):
        return Trade(i, date, jp.id, broker, "特定", action, qty, price)

    moves = [u(1, "2026-01-01", "transfer_in", 200, 1000), u(2, "2026-02-01", "sell", 50, 1200),
             u(3, "2026-03-01", "transfer_out", 150, 0), u(4, "2026-03-01", "transfer_in", 150, 1000, "SBI証券")]
    ledger = build_ledger(moves, instruments)
    assert not ledger.problems, ledger.problems
    assert len(ledger.realized) == 1 and ledger.realized[0].profit == 50 * 200
    assert list(ledger.holdings) == [(jp.id, "SBI証券", "特定")]
    assert ledger.holdings[(jp.id, "SBI証券", "特定")].average_price == 1000
    assert validate_trade(u(5, "2026-03-02", "transfer_out", 10, 0), moves, instruments)  # もう保有がない
    print("core_asset: すべての確認に通りました。")


if __name__ == "__main__":
    _self_test()
