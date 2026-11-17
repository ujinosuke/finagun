"""yfinance で銘柄の日足データ(OHLCV)を取得し、CSV に保存する。

listed_stocks フォルダの最新の銘柄一覧 CSV(例: T2610.csv)に載っている全銘柄(PRO Market を除く)について、
2016年1月以降の日足データを stock_database フォルダに保存する。
保存先のファイル名は、例えば 1234.T なら T1234.csv となる。
既にデータがある銘柄は、保存済みの最終日以降の更新分だけを取得して、ファイルの末尾に追記する。

速く取得するため、複数の銘柄をまとめて(BATCH_SIZE 銘柄ずつ)yfinance に問い合わせる。

使い方:
    python core_gather.py              # 銘柄一覧の全銘柄を取得・更新
    python core_gather.py 7203 6758    # 指定した銘柄だけを取得・更新
"""

import logging
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

BASE_DIR = Path(__file__).resolve().parent
LISTED_STOCKS_DIR = BASE_DIR / "listed_stocks"
STOCK_DATABASE_DIR = BASE_DIR / "stock_database"

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
# 取得しない市場。PRO Market はプロ投資家向けで個人は通常売買できず、Yahoo にデータがない銘柄も多い
EXCLUDED_MARKETS = {"PRO Market"}

# 取得できなかった銘柄について yfinance が出すエラー表示を抑える(結果はこのプログラムで表示する)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


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


def download(codes: list[str], start: str) -> dict[str, pd.DataFrame]:
    """複数の銘柄の start 以降の日足を、まとめて取得する。取得できない銘柄は空の DataFrame。

    auto_adjust=False にして、配当で過去の値が変わらない株価を使う
    (株式分割は Yahoo 側で過去分も調整される)。
    """
    tickers = [f"{code}.T" for code in codes]
    raw = yf.download(
        tickers, start=start, auto_adjust=False, actions=False,
        group_by="ticker", progress=False, threads=True,
    )
    available = set(raw.columns.get_level_values(0)) if not raw.empty else set()
    return {
        code: clean(raw[ticker]) if ticker in available else pd.DataFrame(columns=COLUMNS)
        for code, ticker in zip(codes, tickers)
    }


def same_prices(a: pd.Series, b: pd.Series) -> bool:
    return all(abs(a[c] - b[c]) <= max(abs(a[c]), 1) * 1e-6 for c in COLUMNS)


def in_batches(codes: list[str]):
    for i in range(0, len(codes), BATCH_SIZE):
        yield codes[i: i + BATCH_SIZE]


class Gatherer:
    """銘柄の取得・更新をまとめて行い、結果を記録する。"""

    def __init__(self):
        self.results: dict[str, str] = {}  # コード -> 結果のメッセージ
        self.requests = 0

    def _download(self, codes: list[str], start: str) -> dict[str, pd.DataFrame]:
        if self.requests:
            time.sleep(BATCH_INTERVAL)
        self.requests += 1
        return download(codes, start)

    def fetch_full(self, codes: list[str], reason: str = "新規") -> None:
        """全期間を取得して、ファイルを作る(上書きする)。"""
        for batch in in_batches(codes):
            for code, df in self._download(batch, START_DATE).items():
                if df.empty:
                    self.results[code] = "[失敗] データを取得できません"
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
                        self.results[code] = "[失敗] 更新データを取得できません"
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
        print(f"  ... {len(self.results)} 銘柄 処理済み", flush=True)

    def failed(self) -> list[str]:
        return [code for code, message in self.results.items() if message.startswith("[失敗]")]


def gather(codes: list[str]) -> Gatherer:
    """銘柄を取得・更新する。失敗した銘柄は、少し待ってからもう一度だけ試す。"""
    gatherer = Gatherer()
    for attempt in range(2):
        last_rows = {code: load_last_row(csv_path_for(code)) for code in codes}
        new_codes = [code for code, last in last_rows.items() if last is None]
        existing = {code: last for code, last in last_rows.items() if last is not None}
        gatherer.fetch_full(new_codes)
        gatherer.update(existing)

        codes = gatherer.failed()
        if not codes or attempt == 1:
            break
        print(f"{len(codes)} 銘柄が失敗したので、{RETRY_WAIT} 秒後にもう一度試します")
        time.sleep(RETRY_WAIT)
    return gatherer


def main() -> int:
    STOCK_DATABASE_DIR.mkdir(exist_ok=True)

    if len(sys.argv) > 1:
        codes = [code.upper().removesuffix(".T") for code in sys.argv[1:]]
    else:
        listed_csv = latest_listed_stocks_csv()
        codes = listed_codes(listed_csv)
        print(f"{listed_csv.name} に載っている {len(codes)} 銘柄を取得します")

    started = time.time()
    gatherer = gather(codes)
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
