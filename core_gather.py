"""yfinance で銘柄の日足データ(OHLCV)を取得し、CSV に保存する。

listed_stocks フォルダの最新の銘柄一覧 CSV(例: T2610.csv)に載っている全銘柄(PRO Market を除く)について、
2016年1月以降の日足データを stock_database フォルダに保存する。
保存先のファイル名は、例えば 1234.T なら T1234.csv となる。
既にデータがある銘柄は、保存済みの最終日以降の更新分だけを取得して、ファイルの末尾に追記する。

速く取得するため、複数の銘柄をまとめて(BATCH_SIZE 銘柄ずつ)yfinance に問い合わせる。

ToreCan.py(ホーム画面)からも、別スレッドで実行する(進み具合の通知と中止ができる)。
同じ CSV に 2 か所から書き込まないよう、実行中はルートに gather.lock を置き、同時には実行しない。

使い方:
    python core_gather.py              # 銘柄一覧の全銘柄を取得・更新
    python core_gather.py 7203 6758    # 指定した銘柄だけを取得・更新
"""

import logging
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

try:  # 古い yfinance(0.2 系)は、取得できなかった銘柄のエラーを shared._ERRORS に残す
    import yfinance.shared as yf_shared
except ImportError:
    yf_shared = None

from core_settings import APP_DIR, acquire_lock, data_root, release_lock

# BASE_DIR は保存ファイル(asset.json など)を置くプログラムのフォルダ。
# 株価データ・銘柄一覧のフォルダは、全体設定(settings.json)のデータフォルダの中(既定はプログラムのフォルダ)
BASE_DIR = APP_DIR
LISTED_STOCKS_DIR = data_root() / "listed_stocks"
STOCK_DATABASE_DIR = data_root() / "stock_database"

START_DATE = "2016-01-01"
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
PRICE_COLUMNS = ["Open", "High", "Low", "Close"]
JST = ZoneInfo("Asia/Tokyo")
# この時刻(日本時間)より前は、当日の足が未確定とみなして保存しない
MARKET_DATA_READY_HOUR = 16
# 1 回にまとめて問い合わせる銘柄数
BATCH_SIZE = 100
# Yahoo への連続アクセスを避けるための、まとめた問い合わせの間の待ち時間(秒)
BATCH_INTERVAL = 1.0
# 失敗した銘柄をもう一度試す前の待ち時間(秒)。一時的なエラーやアクセス制限に備える
RETRY_WAIT = 30
# データ更新の実行中に置くロックファイル(同時に 2 か所から更新しないため)
GATHER_LOCK = APP_DIR / "gather.lock"
# 取得しない市場。PRO Market はプロ投資家向けで個人は通常売買できず、Yahoo にデータがない銘柄も多い
EXCLUDED_MARKETS = {"PRO Market"}

class _ErrorCollector(logging.Handler):
    """yfinance が出すエラーを、画面に出さずに集める(失敗の理由として結果に出すため)。

    yfinance 1.x は、取得できなかった銘柄を "['7203.T', '6758.T']: 理由" の形でログに出す。
    通信の失敗は "possibly delisted" としか出ないことがあり、本当の原因は銘柄名のない警告
    ("Cookie/crumb fetch failed (ConnectionError)" など)に出るので、警告も集める。
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(record.getMessage())
        except Exception:  # noqa: BLE001  ログの組み立てに失敗しても、取得は続ける
            pass

    def reasons(self, tickers: list[str]) -> dict[str, str]:
        """集めたエラーから、ティッカー -> 理由 を作る。

        どの銘柄か書いていないエラー・警告(通信の失敗など)は、全銘柄の理由の前に付ける。
        """
        found: dict[str, str] = {}
        general: list[str] = []
        for message in self.messages:
            head, sep, reason = message.partition("]: ")
            if sep and head.lstrip().startswith("["):
                for ticker in tickers:
                    if f"'{ticker}'" in head:
                        found[ticker] = reason
            elif message.strip() and "Failed download" not in message and not message.lstrip().startswith("$"):
                # "$7203.T: ..." は 1 銘柄ごとのエラー(同じ内容が上の形でも出る)なので、全銘柄には広げない
                general.append(message)
        if general:  # 通信のエラー(SSL など)は銘柄名なしで出ることがある
            for ticker in tickers:
                found[ticker] = general[-1] + (f" / {found[ticker]}" if ticker in found else "")
        return found


# 取得できなかった銘柄について yfinance が出すエラー表示を抑え、理由として集める(結果はこのプログラムで表示する)
_yf_errors = _ErrorCollector()
_yf_logger = logging.getLogger("yfinance")
_yf_logger.setLevel(logging.WARNING)
_yf_logger.propagate = False
_yf_logger.addHandler(_yf_errors)


def latest_listed_stocks_csv() -> Path:
    """listed_stocks フォルダで一番新しい年月の銘柄一覧 CSV を返す(T2610.csv など)。"""
    csv_files = sorted(LISTED_STOCKS_DIR.glob("T[0-9][0-9][0-9][0-9].csv"))
    if not csv_files:
        raise FileNotFoundError(
            f"銘柄一覧の CSV がありません: {LISTED_STOCKS_DIR}"
            "(先に core_listup_stocks.py を実行してください)"
        )
    return csv_files[-1]


def listed_codes(listed_csv: Path) -> list[str]:
    """銘柄一覧 CSV に載っている銘柄のコードを返す(EXCLUDED_MARKETS の市場は除く)。"""
    df = pd.read_csv(listed_csv, dtype=str, encoding="utf-8-sig")
    df = df[~df["市場・商品区分"].isin(EXCLUDED_MARKETS)]
    return df["コード"].dropna().tolist()


def csv_path_for(code: str) -> Path:
    return STOCK_DATABASE_DIR / f"T{code}.csv"


def load_saved(csv_path: Path) -> pd.DataFrame | None:
    """保存済みの日足データ全体を読み込む(チャート表示などで使う)。"""
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path, index_col="Date", parse_dates=["Date"])
    return df if not df.empty else None


def load_last_row(csv_path: Path) -> pd.Series | None:
    """保存済みデータの最終行だけを読み込む。ファイル全体を読まないので速い。"""
    if not csv_path.exists():
        return None
    with open(csv_path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(f.tell() - 1024, 0))
        lines = f.read().decode("utf-8").splitlines()
    fields = lines[-1].split(",") if lines else []
    if len(fields) != len(COLUMNS) + 1 or fields[0] == "Date":
        return None  # 見出ししかない、または壊れている
    values = [float(v) for v in fields[1:]]
    return pd.Series(values, index=COLUMNS, name=pd.Timestamp(fields[0]))


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    """yfinance から受け取った 1 銘柄分のデータを、保存する形に整える。"""
    df = raw[COLUMNS].dropna(subset=["Close"]).copy()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = df.index.normalize()
    df.index.name = "Date"
    # 1467.4000244140625 のような浮動小数点の誤差を丸める
    df[PRICE_COLUMNS] = df[PRICE_COLUMNS].round(4)
    df["Volume"] = df["Volume"].fillna(0).astype("int64")

    # 取引時間中や引け直後は当日の足が未確定なので除く
    now = datetime.now(JST)
    if now.hour < MARKET_DATA_READY_HOUR:
        df = df[df.index.date < now.date()]
    return df


def short_reason(message: object) -> str:
    """yfinance のエラーを、画面に出せる長さの 1 行にする。"""
    text = " ".join(str(message).split())
    return text if len(text) <= 120 else text[:117] + "..."


def download(codes: list[str], start: str) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """複数の銘柄の start 以降の日足を、まとめて取得する。取得できない銘柄は空の DataFrame。

    戻り値は (コード -> 日足, コード -> 取得できなかった理由)。理由は yfinance が記録したエラーで、
    通信の失敗(SSL・アクセス制限など)を見分けるのに使う(エラー表示は抑えているため)。

    auto_adjust=False にして、配当で過去の値が変わらない株価を使う
    (株式分割は Yahoo 側で過去分も調整される)。
    """
    tickers = [f"{code}.T" for code in codes]
    _yf_errors.messages.clear()
    raw = yf.download(
        tickers, start=start, auto_adjust=False, actions=False,
        group_by="ticker", progress=False, threads=True,
    )
    # 取得できなかった銘柄の理由。新しい yfinance はログから、古い yfinance は shared._ERRORS から
    errors = _yf_errors.reasons(tickers)
    if yf_shared is not None:
        errors.update(getattr(yf_shared, "_ERRORS", None) or {})
    reasons = {code: short_reason(errors[ticker]) for code, ticker in zip(codes, tickers) if ticker in errors}
    available = set(raw.columns.get_level_values(0)) if not raw.empty else set()
    frames = {
        code: clean(raw[ticker]) if ticker in available else pd.DataFrame(columns=COLUMNS)
        for code, ticker in zip(codes, tickers)
    }
    return frames, reasons


def same_prices(a: pd.Series, b: pd.Series) -> bool:
    return all(abs(a[c] - b[c]) <= max(abs(a[c]), 1) * 1e-6 for c in COLUMNS)


def in_batches(codes: list[str]):
    for i in range(0, len(codes), BATCH_SIZE):
        yield codes[i: i + BATCH_SIZE]


class GatherCancelled(Exception):
    """データ更新が中止された。"""


class GatherBusy(Exception):
    """別の画面(ホームやコマンドライン)でデータ更新を実行中。"""


class Gatherer:
    """銘柄の取得・更新をまとめて行い、結果を記録する。

    on_progress を渡すと、まとめた問い合わせのたびに (処理済みの銘柄数, 全体の銘柄数) を渡して呼ぶ
    (渡さなければ、今までどおり進み具合を表示する)。cancel が立っていたら、次の問い合わせの前で止める
    (GatherCancelled)。問い合わせの切れ目で止めるので、CSV の書き込みの途中では止まらない。
    """

    def __init__(self, on_progress: Callable[[int, int], None] | None = None,
                 cancel: threading.Event | None = None, total: int = 0):
        self.results: dict[str, str] = {}  # コード -> 結果のメッセージ
        self.reasons: dict[str, str] = {}  # コード -> 取得できなかった理由(yfinance のエラー。あるときだけ)
        self.requests = 0
        self.on_progress = on_progress
        self.cancel = cancel
        self.total = total

    def check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise GatherCancelled()

    def _download(self, codes: list[str], start: str) -> dict[str, pd.DataFrame]:
        self.check_cancel()
        if self.requests:
            time.sleep(BATCH_INTERVAL)
        self.requests += 1
        frames, reasons = download(codes, start)
        for code in codes:  # 取り直した銘柄の古い理由は消す
            self.reasons.pop(code, None)
        self.reasons.update(reasons)
        return frames

    def _fail(self, code: str, message: str) -> None:
        """取得の失敗を記録する。yfinance のエラーがあれば、理由として添える。"""
        reason = self.reasons.get(code)
        self.results[code] = f"[失敗] {message}" + (f"({reason})" if reason else "")

    def fetch_full(self, codes: list[str], reason: str = "新規") -> None:
        """全期間を取得して、ファイルを作る(上書きする)。"""
        for batch in in_batches(codes):
            for code, df in self._download(batch, START_DATE).items():
                if df.empty:
                    self._fail(code, "データを取得できません")
                    continue
                df.to_csv(csv_path_for(code))
                self.results[code] = (
                    f"[{reason}] {len(df)} 日分 ({df.index[0]:%Y-%m-%d} - {df.index[-1]:%Y-%m-%d})"
                )
            self._progress()

    def update(self, last_rows: dict[str, pd.Series]) -> None:
        """保存済みの銘柄に、最終日より後の分を追記する。"""
        # 最終日が同じ銘柄どうしを、まとめて問い合わせる
        by_last_date = defaultdict(list)
        for code, last in last_rows.items():
            by_last_date[last.name].append(code)

        refetch = []
        for last_date, codes in sorted(by_last_date.items()):
            for batch in in_batches(codes):
                # 保存済みの最終日から取得し、その日の値が一致するか確かめる
                for code, new in self._download(batch, f"{last_date:%Y-%m-%d}").items():
                    if new.empty or last_date not in new.index:
                        self._fail(code, "更新データを取得できません")
                    elif not same_prices(last_rows[code], new.loc[last_date]):
                        # 株式分割などで過去の値が変わったので、後で全期間を取り直す
                        refetch.append(code)
                    else:
                        added = new[new.index > last_date]
                        if added.empty:
                            self.results[code] = "[最新] 更新分はありません"
                        else:
                            added.to_csv(csv_path_for(code), mode="a", header=False)
                            self.results[code] = f"[更新] {len(added)} 日分を追加 (- {added.index[-1]:%Y-%m-%d})"
                self._progress()

        if refetch:
            self.fetch_full(refetch, reason="再取得")

    def _progress(self) -> None:
        if self.on_progress is not None:
            self.on_progress(len(self.results), self.total)
        else:
            print(f"  ... {len(self.results)} 銘柄 処理済み", flush=True)

    def failed(self) -> list[str]:
        return [code for code, message in self.results.items() if message.startswith("[失敗]")]

    def failure_reasons(self) -> list[tuple[str, int]]:
        """失敗した銘柄の理由と銘柄数を、多い順に返す(理由のわかった銘柄だけ)。"""
        counts = Counter(self.reasons[code] for code in self.failed() if code in self.reasons)
        return counts.most_common()


def gather(codes: list[str], on_progress: Callable[[int, int], None] | None = None,
           cancel: threading.Event | None = None) -> Gatherer:
    """銘柄を取得・更新する。失敗した銘柄は、少し待ってからもう一度だけ試す。

    別の画面で実行中なら GatherBusy、中止されたら GatherCancelled を投げる。
    """
    if not acquire_lock(GATHER_LOCK):
        raise GatherBusy("別の画面(ホームやコマンドライン)でデータ更新を実行中です")
    try:
        return _gather(codes, Gatherer(on_progress, cancel, len(codes)))
    finally:
        release_lock(GATHER_LOCK)


def _gather(codes: list[str], gatherer: Gatherer) -> Gatherer:
    for attempt in range(2):
        last_rows = {code: load_last_row(csv_path_for(code)) for code in codes}
        new_codes = [code for code, last in last_rows.items() if last is None]
        existing = {code: last for code, last in last_rows.items() if last is not None}
        gatherer.fetch_full(new_codes)
        gatherer.update(existing)

        codes = gatherer.failed()
        if not codes or attempt == 1:
            break
        if gatherer.on_progress is None:
            print(f"{len(codes)} 銘柄が失敗したので、{RETRY_WAIT} 秒後にもう一度試します")
        # 待っている間も中止が効くよう、少しずつ待つ
        for _ in range(int(RETRY_WAIT / 0.5)):
            gatherer.check_cancel()
            time.sleep(0.5)
    return gatherer


# ---- データの状態(ToreCan のホーム画面で表示する) ----

@dataclass(frozen=True)
class DataStatus:
    """保存済みの株価データと銘柄一覧の状態。"""
    files: int                 # 収録銘柄数(stock_database の T*.csv の数)
    latest: str | None         # 一番多くの銘柄がそろっている最終日("市場の最終日"とみなす)
    behind: int                # 最終日が latest より古い銘柄数(売買停止・上場廃止・取得失敗など)
    expected: str              # 本来そろっているはずの最終日(expected_last_day)
    listed_name: str | None    # 最新の銘柄一覧(例 "T2610")
    listed_missing: int        # 銘柄一覧にあるのに CSV がない銘柄数

    @property
    def up_to_date(self) -> bool:
        return self.latest is not None and self.latest >= self.expected


def expected_last_day(now: datetime | None = None) -> str:
    """データがそろっているはずの最終日。日本時間で MARKET_DATA_READY_HOUR 時より前なら前の平日まで。

    祝日は考えない(祝日の翌日などは「更新が必要」と出ることがあるが、更新しても「更新分なし」になるだけ)。
    """
    now = now or datetime.now(JST)
    day = now.date() - timedelta(days=1 if now.hour < MARKET_DATA_READY_HOUR else 0)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def listed_month(name: str) -> tuple[int, int] | None:
    """銘柄一覧の名前 "T2610" -> (2026, 10)。"""
    try:
        return 2000 + int(name[1:3]), int(name[3:5])
    except (ValueError, IndexError):
        return None


def data_status(now: datetime | None = None) -> DataStatus:
    """株価データの最終日・銘柄数と、銘柄一覧の状態を調べる(各 CSV の末尾だけを読む。数秒かかる)。"""
    paths = sorted(STOCK_DATABASE_DIR.glob("T*.csv")) if STOCK_DATABASE_DIR.is_dir() else []
    last_days = Counter()
    for path in paths:
        try:
            last = load_last_row(path)
        except (OSError, ValueError, UnicodeDecodeError):
            last = None
        last_days[f"{last.name:%Y-%m-%d}" if last is not None else ""] += 1
    dated = {day: n for day, n in last_days.items() if day}
    latest = max(dated, key=lambda day: (dated[day], day)) if dated else None
    behind = sum(n for day, n in last_days.items() if latest is None or day < latest)
    try:
        listed_csv = latest_listed_stocks_csv()
        saved = {p.stem[1:] for p in paths}
        missing = sum(1 for code in listed_codes(listed_csv) if code not in saved)
        name = listed_csv.stem
    except (FileNotFoundError, OSError, KeyError):
        name, missing = None, 0
    return DataStatus(len(paths), latest, behind, expected_last_day(now), name, missing)


def main() -> int:
    STOCK_DATABASE_DIR.mkdir(exist_ok=True)

    if len(sys.argv) > 1:
        codes = [code.upper().removesuffix(".T") for code in sys.argv[1:]]
    else:
        listed_csv = latest_listed_stocks_csv()
        codes = listed_codes(listed_csv)
        print(f"{listed_csv.name} に載っている {len(codes)} 銘柄を取得します")

    started = time.time()
    try:
        gatherer = gather(codes)
    except GatherBusy as e:
        print(e)
        return 2
    elapsed = time.time() - started

    # 結果: 銘柄数が少なければ 1 銘柄ずつ、多ければ種類ごとの件数を表示する
    if len(codes) <= 20:
        for code in codes:
            print(f"{code}.T: {gatherer.results[code]}")
    counts = defaultdict(int)
    for message in gatherer.results.values():
        counts[message.split("]")[0] + "]"] += 1
    print("結果: " + "、".join(f"{kind} {n} 銘柄" for kind, n in counts.items()) + f"(所要 {elapsed:.0f} 秒)")

    failed = gatherer.failed()
    if failed:
        print("失敗した銘柄: " + " ".join(failed))
        for reason, n in gatherer.failure_reasons()[:3]:
            print(f"  理由({n} 銘柄): {reason}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
