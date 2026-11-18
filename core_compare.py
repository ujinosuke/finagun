"""銘柄比較(fina_compare.py)の計算(GUI を含まない部分)。

設計は fina_compare_design.md、仕様は compare-extension-spec.md を参照。

テクニカル指標
    保存済みの日足 CSV(stock_database)から計算する(外部からは取得しない)。
    価格は終値(Close)だけを使う。例外は 52 週高値・安値(高値・安値)と ATR(高値・安値・終値)。
相関・ベータ
    2 銘柄ごとに、両方に値がある日だけで終値をそろえ、前日比リターンの相関係数・ベータを出す。
    使えるリターンが MIN_DAYS 日より少ない、または値動きがない(分散 0)なら算出不能(None)。
業績
    yfinance の年次の財務諸表(income_stmt)から、売上高・営業利益・純利益を取得し、
    financials_cache.json にキャッシュする(30 日ごとに取り直す)。

使い方(他のプログラムから):
    daily = core_gather.load_saved(csv_path_for("7203"))
    tech = technical_metrics(daily, bench_close)
    corr = pair_correlation(close_a, close_b, window=250)
"""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from core_gather import BASE_DIR
from core_indicators import sma
from core_oscillators import RSI, rma

TRADING_DAYS = 250   # 1 年の営業日数(年率換算・1 年の期間に使う)
MIN_DAYS = 20        # 相関・ベータの計算に最低限要るリターンの日数
FINANCIALS_CACHE_PATH = BASE_DIR / "financials_cache.json"
FINANCIALS_REFRESH_DAYS = 30
FORMAT_VERSION = 1

# 取得できなかった銘柄について yfinance が出すエラー表示を抑える(結果はこのプログラムで表示する)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# 相関の期間(表示名 -> リターンの日数。None は全期間)
CORRELATION_PERIODS = {"3か月": 60, "6か月": 120, "1年": 250, "全期間": None}

# ベンチマークの候補(コード -> 連動する指数)。先頭が既定
BENCHMARKS = {
    "1306": "TOPIX",
    "1321": "日経225",
    "1591": "JPX日経400",
    "2516": "東証グロース市場250",
    "1343": "東証REIT指数",
    "1577": "日本株高配当70",
}


# ---- 株価データの読み込み ----

GLITCH_RATIO = 2.0     # 前日の終値の 2 倍より上、または 1/2 より下に飛んだ日を、異常値の候補にする
GLITCH_MAX_DAYS = 5    # その後この日数以内に元の水準(GLITCH_RETURN の幅)へ戻ったら、異常値とみなす
GLITCH_RETURN = 1.25


def drop_glitches(daily: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """一時的な異常値(株価が数日だけ 1/10 になって戻る、など)の日を除く。(除いたあとの日足, 除いた日) を返す。

    yfinance のデータには、分割の調整の誤りなどで、数日だけ桁の違う値が入ることがある
    (例: 1306 の 2026-03-30・31 が 1/10)。そのままだと相関・ベータ・ボラティリティ・52 週安値などが壊れる。
    1 日で大きく動いて、その水準のまま続く値動き(本当の急騰・急落)は除かない。
    """
    close = daily["Close"].to_numpy(dtype=float)
    drop = np.zeros(len(close), dtype=bool)
    i = 1
    while i < len(close):
        before = close[i - 1]
        ratio = close[i] / before if before > 0 else 1.0
        if ratio > GLITCH_RATIO or ratio < 1 / GLITCH_RATIO:
            # 異常値の候補の日から、元の水準に戻る日を探す
            for j in range(i, min(i + GLITCH_MAX_DAYS, len(close) - 1)):
                back = close[j + 1] / before
                if 1 / GLITCH_RETURN <= back <= GLITCH_RETURN:
                    drop[i:j + 1] = True
                    i = j + 1
                    break
        i += 1
    dropped = [f"{d:%Y-%m-%d}" for d in daily.index[drop]]
    return daily[~drop], dropped


# ---- テクニカル指標 ----

@dataclass(frozen=True)
class TechMetrics:
    """1 銘柄のテクニカル指標。values は 列の名前 -> 値(計算できない値は None)。"""
    values: dict[str, float | None] = field(default_factory=dict)
    base_date: str = ""      # 計算の基準日(CSV の最終日)
    extras: dict[str, float | None] = field(default_factory=dict)  # Excel 出力用の補助の値(円)
    error: str = ""          # 計算できなかった理由(株価データがない、など)


def period_return(close: pd.Series, months: int) -> float | None:
    """months か月前の同じ日(その日がなければ直前の営業日)の終値から、最新の終値までの騰落率(%)。"""
    base_date = close.index[-1] - pd.DateOffset(months=months)
    before = close[close.index <= base_date]
    if len(before) == 0 or before.iloc[-1] <= 0:
        return None
    return (close.iloc[-1] / before.iloc[-1] - 1) * 100


def _last(series: pd.Series) -> float | None:
    if len(series) == 0:
        return None
    value = float(series.iloc[-1])
    return value if math.isfinite(value) else None


def technical_metrics(daily: pd.DataFrame | None, bench_close: pd.Series | None = None) -> TechMetrics:
    """日足(OHLCV)からテクニカル指標を計算する。bench_close はベータの相手(ベンチマークの終値)。"""
    if daily is None or len(daily) == 0 or "Close" not in daily:
        return TechMetrics(error="株価データがありません")
    daily = daily.dropna(subset=["Close"])
    if len(daily) < 2:
        return TechMetrics(error="株価データが足りません")
    close = daily["Close"]
    price = float(close.iloc[-1])
    values: dict[str, float | None] = {}
    extras: dict[str, float | None] = {}

    # 騰落率
    for column, months in (("ret_1m", 1), ("ret_3m", 3), ("ret_6m", 6), ("ret_1y", 12)):
        values[column] = period_return(close, months)

    # 52 週高値・安値(例外: 日中の高値・安値を使う。上場から 1 年たっていなければ、上場来の値)
    year = daily[daily.index > close.index[-1] - pd.DateOffset(years=1)]
    high = year["High"].dropna().max() if "High" in year else np.nan
    low = year["Low"].dropna().min() if "Low" in year else np.nan
    extras["high52"] = float(high) if math.isfinite(high) else None
    extras["low52"] = float(low) if math.isfinite(low) else None
    values["high52"] = price / high * 100 if math.isfinite(high) and high > 0 else None
    values["low52"] = price / low * 100 if math.isfinite(low) and low > 0 else None

    # 移動平均からの乖離率
    for period in (25, 75, 200):
        average = _last(sma(close, period))
        extras[f"sma{period}"] = average
        values[f"dev{period}"] = (price / average - 1) * 100 if average else None

    # RSI(14)。gui_chart.py のオシレーターと同じ計算
    values["rsi14"] = _last(RSI().compute(daily)["RSI"])

    # ボラティリティ(日次の対数リターンの標準偏差を年率換算)
    log_returns = np.log(close).diff().dropna()
    for column, period in (("vol20", 20), ("vol60", 60)):
        recent = log_returns.tail(period)
        values[column] = float(recent.std(ddof=1)) * math.sqrt(TRADING_DAYS) * 100 if len(recent) >= period else None

    # 平均売買代金(20 日。終値 × 出来高。百万円)
    if "Volume" in daily and len(daily) >= 20:
        values["turnover20"] = float((daily["Close"] * daily["Volume"]).tail(20).mean()) / 1e6
    else:
        values["turnover20"] = None

    # ATR(14)(例外: 高値・安値・終値を使う)。TradingView の ta.atr と同じ RMA
    atr = None
    if {"High", "Low"} <= set(daily.columns):
        previous = close.shift(1)
        true_range = pd.concat([
            daily["High"] - daily["Low"], (daily["High"] - previous).abs(), (daily["Low"] - previous).abs(),
        ], axis=1).max(axis=1, skipna=False)
        true_range.iloc[0] = daily["High"].iloc[0] - daily["Low"].iloc[0]
        atr = _last(rma(true_range, 14))
    extras["atr14"] = atr
    values["atr14"] = atr / price * 100 if atr is not None and price > 0 else None

    # ベータ(1 年)
    values["beta"] = beta(close, bench_close, TRADING_DAYS).value if bench_close is not None else None
    return TechMetrics(values, f"{close.index[-1]:%Y-%m-%d}", extras)


# ---- 相関・ベータ ----

@dataclass(frozen=True)
class Correlation:
    """相関係数(またはベータ)と、計算に使ったリターンの日数。算出不能なら value は None。"""
    value: float | None
    days: int


def paired_returns(close_a: pd.Series, close_b: pd.Series, window: int | None) -> pd.DataFrame:
    """2 銘柄の終値を、両方に値がある日だけでそろえてから、前日比リターンにする。

    先にそろえてからリターンを作るので、片方が売買停止で抜けた日があっても、同じ区間のリターンで比べられる。
    window があれば、最後の window 日分だけにする。
    """
    joined = pd.concat([close_a.rename("a"), close_b.rename("b")], axis=1, join="inner").dropna()
    joined = joined[(joined > 0).all(axis=1)]
    returns = joined.pct_change().iloc[1:]
    return returns.tail(window) if window else returns


def pair_correlation(close_a: pd.Series, close_b: pd.Series, window: int | None) -> Correlation:
    """2 銘柄の日次リターンの相関係数(ピアソン)。"""
    returns = paired_returns(close_a, close_b, window)
    days = len(returns)
    if days < MIN_DAYS:
        return Correlation(None, days)
    a, b = returns["a"].to_numpy(), returns["b"].to_numpy()
    if np.std(a) == 0 or np.std(b) == 0:
        return Correlation(None, days)
    return Correlation(float(np.corrcoef(a, b)[0, 1]), days)


def beta(close: pd.Series, bench: pd.Series | None, window: int | None) -> Correlation:
    """ベンチマークに対するベータ = Cov(銘柄, ベンチマーク) / Var(ベンチマーク)。"""
    if bench is None:
        return Correlation(None, 0)
    returns = paired_returns(close, bench, window)
    days = len(returns)
    variance = float(returns["b"].var(ddof=1)) if days >= 2 else 0.0
    if days < MIN_DAYS or variance == 0 or np.std(returns["a"].to_numpy()) == 0:
        return Correlation(None, days)
    return Correlation(float(returns["a"].cov(returns["b"], ddof=1)) / variance, days)


def correlation_matrix(closes: dict[str, pd.Series], window: int | None) -> dict[tuple[str, str], Correlation]:
    """銘柄 × 銘柄の相関。(a, b) と (b, a) の両方を入れる。同じ銘柄どうしは計算しない(入れない)。"""
    codes = list(closes)
    result = {}
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            value = pair_correlation(closes[a], closes[b], window)
            result[(a, b)] = result[(b, a)] = value
    return result


# ---- 業績 ----

ITEMS = {"revenue": "売上高", "operating_income": "営業利益", "net_income": "純利益"}
# 項目 -> income_stmt の行(先にあるものを使う)。営業利益は EBIT で代用しない(中身が違うため)
STATEMENT_ROWS = {
    "revenue": ("Total Revenue",),
    "operating_income": ("Operating Income",),
    "net_income": ("Net Income", "Net Income Common Stockholders"),
}


@dataclass(frozen=True)
class FiscalYear:
    """1 期分の業績(通貨は Financials.currency)。"""
    end: str                         # 決算期末 "2026-03-31"
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None

    def item(self, name: str) -> float | None:
        return getattr(self, name)

    @property
    def operating_margin(self) -> float | None:
        """営業利益率(%)。"""
        if self.revenue is None or self.operating_income is None or self.revenue <= 0:
            return None
        return self.operating_income / self.revenue * 100


@dataclass(frozen=True)
class Financials:
    """1 銘柄の年次の業績(古い順)。"""
    code: str
    years: list[FiscalYear]
    currency: str
    fetched_at: str
    error: str = ""


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def fetch_financials(code: str) -> Financials:
    """yfinance から 1 銘柄の年次の業績を取得する。取得できなければ error を入れて返す(例外は出さない)。"""
    now = datetime.now().isoformat(timespec="seconds")
    try:
        ticker = yf.Ticker(f"{code}.T")
        statement = ticker.income_stmt
        try:
            currency = ticker.info.get("financialCurrency") or "JPY"
        except Exception:  # noqa: BLE001  通貨が取れなくても業績は使う
            currency = "JPY"
    except Exception as e:  # noqa: BLE001  通信の失敗など
        return Financials(code, [], "JPY", now, f"{type(e).__name__}: {e}")
    if statement is None or statement.empty:
        return Financials(code, [], currency, now, "財務諸表のデータがありません")
    years = []
    for end in statement.columns:
        values = {}
        for item, rows in STATEMENT_ROWS.items():
            values[item] = next((v for row in rows if row in statement.index
                                 and (v := _number(statement.at[row, end])) is not None), None)
        if all(v is None for v in values.values()):
            continue  # 古い期で中身がないことがある
        years.append(FiscalYear(f"{pd.Timestamp(end):%Y-%m-%d}", **values))
    years.sort(key=lambda y: y.end)
    if not years:
        return Financials(code, [], currency, now, "売上高・営業利益・純利益が取れません")
    return Financials(code, years, currency, now)


def growth(previous: float | None, current: float | None) -> float | None:
    """前期比(%)。前の期が 0 以下なら None(赤字からの増減は % で表せないため)。"""
    if previous is None or current is None or previous <= 0:
        return None
    return (current / previous - 1) * 100


def cagr(first: float | None, last: float | None, years: int) -> float | None:
    """年平均成長率(%)。どちらかが 0 以下なら None。"""
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


def aligned_years(financials: Financials | None, count: int) -> list[FiscalYear | None]:
    """「何期前か」でそろえた count 期分(古い順。最後が直近期)。足りない古い期は None。"""
    years = financials.years[-count:] if financials else []
    return [None] * (count - len(years)) + list(years)


def indexed(values: list[float | None]) -> tuple[list[float | None], str]:
    """値のある最初の期を 100 にした指数。指数にできなければ ([], 理由)。"""
    base = next((v for v in values if v is not None), None)
    if base is None:
        return [], "値がありません"
    if base <= 0:
        return [], "基準の期の値が 0 以下のため、指数化できません"
    return [None if v is None else v / base * 100 for v in values], ""


class FinancialsCache:
    """financials_cache.json の読み書き。"""

    def __init__(self, path: Path = FINANCIALS_CACHE_PATH):
        self.path = path
        self.items: dict[str, Financials] = {}

    def load(self) -> None:
        """ファイルを読み込む。ない・壊れているときは空のまま(キャッシュなので取り直せばよい)。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        items = data.get("items") if isinstance(data, dict) else None
        for code, saved in (items.items() if isinstance(items, dict) else []):
            parsed = _parse_financials(code, saved)
            if parsed is not None:
                self.items[code] = parsed

    def save(self) -> None:
        data = {"version": FORMAT_VERSION, "items": {
            code: {
                "currency": f.currency, "fetched_at": f.fetched_at, "error": f.error,
                "years": [[y.end, y.revenue, y.operating_income, y.net_income] for y in f.years],
            }
            for code, f in sorted(self.items.items())
        }}
        temporary = self.path.with_name(self.path.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(temporary, self.path)

    def get(self, code: str) -> Financials | None:
        return self.items.get(code)

    def stale_codes(self, codes: list[str], refresh_days: int = FINANCIALS_REFRESH_DAYS,
                    now: datetime | None = None) -> list[str]:
        """キャッシュにない銘柄と、取得から refresh_days 日より古い銘柄(重複なし、渡した順)。"""
        limit = ((now or datetime.now()) - timedelta(days=refresh_days)).isoformat(timespec="seconds")
        return [c for c in dict.fromkeys(codes) if c not in self.items or self.items[c].fetched_at < limit]


def _parse_financials(code, saved) -> Financials | None:
    if not isinstance(code, str) or not isinstance(saved, dict) or not isinstance(saved.get("fetched_at"), str):
        return None
    years = []
    for item in saved.get("years") if isinstance(saved.get("years"), list) else []:
        if isinstance(item, list) and len(item) == 4 and isinstance(item[0], str):
            years.append(FiscalYear(item[0], *(_number(v) for v in item[1:])))
    currency = saved.get("currency") if isinstance(saved.get("currency"), str) else "JPY"
    error = saved.get("error") if isinstance(saved.get("error"), str) else ""
    return Financials(code, sorted(years, key=lambda y: y.end), currency, saved["fetched_at"], error)


def _self_test() -> None:
    """計算を確かめる(python core_compare.py で実行。保存済みの株価データを使う)。"""
    from core_gather import csv_path_for, load_saved

    index = pd.bdate_range("2024-01-01", periods=300)
    rng = np.random.default_rng(0)
    a = pd.Series(np.exp(np.cumsum(rng.normal(0, 0.01, 300))) * 100, index=index)
    noise = pd.Series(np.exp(np.cumsum(rng.normal(0, 0.01, 300))) * 100, index=index)
    # 同じ値動きなら相関 1、ベータ 1
    assert abs(pair_correlation(a, a * 2, 250).value - 1) < 1e-9
    assert abs(beta(a * 3, a, 250).value - 1) < 1e-9
    assert pair_correlation(a, a * 2, 250).days == 250
    # 値動きがない銘柄・重なる期間が短い銘柄は算出不能
    flat = pd.Series(100.0, index=index)
    assert pair_correlation(a, flat, None).value is None
    short = a.iloc[-10:]
    result = pair_correlation(a, short, None)
    assert result.value is None and result.days == 9, result
    # 新規上場(期間が短い)なら、重なる期間に合わせる
    recent = noise.iloc[-100:]
    assert pair_correlation(a, recent, 250).days == 99
    # 片方が抜けた日があっても、そろえてから計算する
    gappy = noise.drop(noise.index[50:55])
    assert pair_correlation(a, gappy, None).days == 300 - 5 - 1

    # 数日だけ 1/10 になって戻る値は除き、本当の急騰(その水準が続く)は残す
    prices = pd.DataFrame({"Close": [100, 101, 10, 10.2, 102, 103, 250, 251, 252]},
                          index=pd.bdate_range("2026-01-01", periods=9))
    cleaned, dropped = drop_glitches(prices)
    assert dropped == ["2026-01-05", "2026-01-06"], dropped
    assert list(cleaned["Close"]) == [100, 101, 102, 103, 250, 251, 252]

    daily, _ = drop_glitches(load_saved(csv_path_for("7203")))
    bench, bench_dropped = drop_glitches(load_saved(csv_path_for("1306")))
    print("1306 の異常値として除いた日:", bench_dropped)
    tech = technical_metrics(daily, bench["Close"])
    assert not tech.error, tech.error
    rsi = RSI().compute(daily)["RSI"].iloc[-1]
    assert abs(tech.values["rsi14"] - rsi) < 1e-9
    assert tech.values["high52"] <= 100 + 1e-9 and tech.values["low52"] >= 100 - 1e-9
    print("7203", tech.base_date, {k: (round(v, 2) if v is not None else None) for k, v in tech.values.items()})
    print("core_compare: すべての確認に通りました。")


if __name__ == "__main__":
    _self_test()
