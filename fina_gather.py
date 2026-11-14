"""yfinance で東証プライム銘柄の日足データ(OHLCV)を取得し、CSV に保存する。

listed_stocks フォルダの最新の銘柄一覧 CSV(例: T2610.csv)から東証プライムの銘柄を選び、
2016年1月以降の日足データを stock_database フォルダに保存する。
保存先のファイル名は、例えば 1234.T なら T1234.csv となる。
既にデータがある銘柄は、保存済みの最終日以降の更新分だけを取得する。

使い方:
    python fina_gather.py              # 東証プライム全銘柄を取得・更新
    python fina_gather.py 7203 6758    # 指定した銘柄だけを取得・更新
"""

import sys
import time
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
JST = ZoneInfo("Asia/Tokyo")
# この時刻(日本時間)より前は、当日の足が未確定とみなして保存しない
MARKET_DATA_READY_HOUR = 16
# Yahoo への連続アクセスを避けるための待ち時間(秒)
REQUEST_INTERVAL = 0.5


def latest_listed_stocks_csv() -> Path:
    """listed_stocks フォルダで一番新しい年月の銘柄一覧 CSV を返す(T2610.csv など)。"""
    csv_files = sorted(LISTED_STOCKS_DIR.glob("T[0-9][0-9][0-9][0-9].csv"))
    if not csv_files:
        raise FileNotFoundError(
            f"銘柄一覧の CSV がありません: {LISTED_STOCKS_DIR}"
            "(先に fina_listup_stocks.py を実行してください)"
        )
    return csv_files[-1]


def prime_codes(listed_csv: Path) -> list[str]:
    """銘柄一覧 CSV から東証プライム銘柄のコードを返す。"""
    df = pd.read_csv(listed_csv, dtype=str, encoding="utf-8-sig")
    prime = df[df["市場・商品区分"].str.startswith("プライム", na=False)]
    return prime["コード"].tolist()


def csv_path_for(code: str) -> Path:
    return STOCK_DATABASE_DIR / f"T{code}.csv"


def load_saved(csv_path: Path) -> pd.DataFrame | None:
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path, index_col="Date", parse_dates=["Date"])
    return df if not df.empty else None


def download(code: str, start: str) -> pd.DataFrame:
    """yfinance から start 以降の日足 OHLCV を取得する。

    auto_adjust=False にして、配当で過去の値が変わらない株価を使う
    (株式分割は Yahoo 側で過去分も調整される)。
    """
    hist = yf.Ticker(f"{code}.T").history(start=start, auto_adjust=False)
    if hist.empty:
        return hist
    df = hist[COLUMNS].copy()
    df.index = df.index.tz_localize(None).normalize()
    df.index.name = "Date"
    # 1467.4000244140625 のような浮動小数点の誤差を丸める
    df[["Open", "High", "Low", "Close"]] = df[["Open", "High", "Low", "Close"]].round(4)
    df["Volume"] = df["Volume"].astype("int64")

    # 取引時間中や引け直後は当日の足が未確定なので除く
    now = datetime.now(JST)
    if now.hour < MARKET_DATA_READY_HOUR:
        df = df[df.index.date < now.date()]
    return df


def same_prices(a: pd.Series, b: pd.Series) -> bool:
    return all(abs(a[c] - b[c]) <= max(abs(a[c]), 1) * 1e-6 for c in COLUMNS)


def gather(code: str) -> str:
    """1 銘柄を取得・更新して、結果のメッセージを返す。"""
    csv_path = csv_path_for(code)
    saved = load_saved(csv_path)

    if saved is None:
        new = download(code, START_DATE)
        if new.empty:
            return "[失敗] データを取得できません"
        new.to_csv(csv_path)
        return f"[新規] {len(new)} 日分 ({new.index[0]:%Y-%m-%d} - {new.index[-1]:%Y-%m-%d})"

    # 保存済みの最終日から取得し、その日の値が一致するか確かめる
    last_date = saved.index[-1]
    new = download(code, f"{last_date:%Y-%m-%d}")
    if new.empty or last_date not in new.index:
        return "[失敗] 更新データを取得できません"

    if not same_prices(saved.loc[last_date], new.loc[last_date]):
        # 株式分割などで過去の値が変わったので、全期間を取り直す
        full = download(code, START_DATE)
        if full.empty:
            return "[失敗] 全期間の再取得ができません"
        full.to_csv(csv_path)
        return f"[再取得] 過去の値が変わったため全期間を取り直しました ({len(full)} 日分)"

    added = new[new.index > last_date]
    if added.empty:
        return "[最新] 更新分はありません"
    pd.concat([saved, added]).to_csv(csv_path)
    return f"[更新] {len(added)} 日分を追加 (- {added.index[-1]:%Y-%m-%d})"


def main() -> int:
    STOCK_DATABASE_DIR.mkdir(exist_ok=True)

    if len(sys.argv) > 1:
        codes = sys.argv[1:]
    else:
        listed_csv = latest_listed_stocks_csv()
        codes = prime_codes(listed_csv)
        print(f"{listed_csv.name} から東証プライム {len(codes)} 銘柄を取得します")

    failed = 0
    for i, code in enumerate(codes, 1):
        try:
            message = gather(code)
        except Exception as e:  # 1 銘柄の失敗で全体を止めない
            message = f"[失敗] {e}"
        if message.startswith("[失敗]"):
            failed += 1
        print(f"({i}/{len(codes)}) {code}.T: {message}")
        if i < len(codes):
            time.sleep(REQUEST_INTERVAL)

    if failed:
        print(f"{failed} 銘柄が失敗しました")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
