"""インジケーターの基本クラスとその種類。

インジケーターは「計算」と「線の見た目」だけを受け持ち、描画は gui_chart.py などの表示側が行う。
バックテスト(fina_bktest.py)やストラテジー(core_strategies.py)からも、compute() で値だけを使える。

新しいインジケーターの作り方:
    1. Indicator を継承する
    2. name と lines(列名 -> 線の見た目)を決める
    3. compute() で、足と同じ行・lines と同じ列の DataFrame を返す

インジケーターは、渡された足(日足・週足・月足)の 1 本を 1 期間として計算する。
たとえば週足に SMA25 を使えば 25 週の移動平均になる。

価格は終値(Close)だけを使う。例外は ATR(定義どおり高値・安値・終値を使う)。
出来高の移動平均・急増率は出来高(Volume)を使う。

計算の関数(sma・ema・rma・macd・bollinger・atr・volume_ma・volume_surge など)は、
ストラテジー・コンディション・スクリーニングからも直接使える。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LineStyle:
    """インジケーターの線 1 本の見た目。"""
    label: str
    color: str
    width: float


@dataclass(frozen=True)
class FillStyle:
    """インジケーターの 2 本の線(upper と lower の列)の間の塗り。alpha は透明度(0 で透明)。"""
    upper: str
    lower: str
    color: str
    alpha: float


class Indicator(ABC):
    """インジケーターの基本クラス。

    bars は Open / High / Low / Close / Volume の列を持ち、日付が行の DataFrame。
    """

    name: str = ""
    # 列名 -> 線の見た目。compute() が返す列と一致させる
    lines: dict[str, LineStyle] = {}
    # 2 本の線の間の塗り(ボリンジャーバンドなど)。列名は lines のもの
    fills: tuple["FillStyle", ...] = ()

    @abstractmethod
    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        """bars と同じ行を持ち、lines の列名を列とする DataFrame を返す。

        計算に必要な本数がそろわない最初の行は NaN にする。
        """


def sma(series: pd.Series, period: int) -> pd.Series:
    """単純移動平均(SMA)。最初の period - 1 本は NaN になる。"""
    return series.rolling(period, min_periods=period).mean()


def _smoothed(series: pd.Series, period: int, step) -> pd.Series:
    """先頭 period 本の単純平均を最初の値にし、以降は step(前回の値, 今回の値) で更新する。

    ema・rma の共通部分。先頭の NaN は飛ばす。
    """
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if valid.size:
        seed = valid[0] + period - 1
        if seed < len(values):
            out[seed] = values[valid[0]: seed + 1].mean()
            for i in range(seed + 1, len(values)):
                out[i] = step(out[i - 1], values[i])
    return pd.Series(out, index=series.index)


def ema(series: pd.Series, period: int) -> pd.Series:
    """指数移動平均(EMA)。係数は 2 / (period + 1)。

    最初の値は先頭 period 本の単純平均(TradingView の ta.ema と同じ)。最初の period - 1 本は NaN。
    """
    alpha = 2 / (period + 1)
    return _smoothed(series, period, lambda prev, value: alpha * value + (1 - alpha) * prev)


def rma(series: pd.Series, period: int) -> pd.Series:
    """ワイルダーの移動平均(RMA)。RSI・ATR などで使う。

    最初の値は先頭 period 本の単純平均で、以降は
    「前回の値 × (period - 1) / period + 今回の値 / period」で更新する。
    TradingView の ta.rma と同じ計算。
    """
    return _smoothed(series, period, lambda prev, value: (prev * (period - 1) + value) / period)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD。列は MACD(短期 EMA - 長期 EMA)・Signal(MACD の EMA)・Hist(MACD - Signal)。

    TradingView の ta.macd と同じ(既定 12 / 26 / 9)。
    """
    line = ema(close, fast) - ema(close, slow)
    signal_line = ema(line, signal)
    return pd.DataFrame({"MACD": line, "Signal": signal_line, "Hist": line - signal_line}, index=close.index)


def bollinger(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.DataFrame:
    """ボリンジャーバンド。列は Upper・Middle(SMA)・Lower。

    σ は母標準偏差(期間 period 本、TradingView の ta.stdev と同じ)。Upper / Lower は Middle ± k × σ。
    """
    middle = sma(close, period)
    sigma = close.rolling(period, min_periods=period).std(ddof=0)
    return pd.DataFrame({"Upper": middle + k * sigma, "Middle": middle, "Lower": middle - k * sigma},
                        index=close.index)


def bollinger_bandwidth(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.Series:
    """バンド幅(%)。(Upper - Lower) / Middle × 100。小さいほどバンドが縮んでいる。"""
    bands = bollinger(close, period, k)
    return (bands["Upper"] - bands["Lower"]) / bands["Middle"] * 100


def bollinger_squeeze(close: pd.Series, period: int = 20, k: float = 2.0, lookback: int = 125) -> pd.Series:
    """スクイーズの判定。バンド幅が直近 lookback 本(既定 125 本 ≒ 半年)でいちばん小さい日を True にする。

    バンド幅が lookback 本そろわない最初のうちは False。
    """
    width = bollinger_bandwidth(close, period, k)
    lowest = width.rolling(lookback, min_periods=lookback).min()
    return lowest.notna() & (width <= lowest)


def true_range(bars: pd.DataFrame) -> pd.Series:
    """真の値幅(TR)。高値 - 安値・|高値 - 前日終値|・|安値 - 前日終値| のいちばん大きいもの。

    例外: 定義どおり高値・安値・終値を使う。最初の足は前日終値がないので 高値 - 安値。
    """
    high, low, previous = bars["High"], bars["Low"], bars["Close"].shift(1)
    tr = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1, skipna=False)
    if len(tr):
        tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return tr


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(平均真の値幅)。TR の RMA(TradingView の ta.atr と同じ)。最初の period - 1 本は NaN。

    スイングの損切り幅の目安に使う(例: 買値 - 2 × ATR)。
    """
    return rma(true_range(bars), period)


def volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
    """出来高の単純移動平均。その日の出来高も含む。"""
    return sma(volume, period)


def volume_surge(volume: pd.Series, period: int = 20) -> pd.Series:
    """出来高の急増率(倍)。その日の出来高 ÷ 前日までの period 本の出来高の平均。

    その日を平均に含めると急増が薄まるので、前日までで比べる。2.0 なら平均の 2 倍。
    前日までの平均が 0 のときは NaN。
    """
    average = sma(volume, period).shift(1)
    return volume / average.where(average > 0)


class SMAEssential4(Indicator):
    """基本の 4 本の単純移動平均線(5 / 25 / 75 / 200)。終値で計算する。

    5  : 青の細い線
    25 : 緑の細い線
    75 : 紫の中くらいの線
    200: 赤の太い線
    """

    name = "SMA 5/25/75/200"
    PERIODS = (5, 25, 75, 200)
    lines = {
        "SMA5": LineStyle("SMA5", "#1e88e5", 0.8),
        "SMA25": LineStyle("SMA25", "#43a047", 0.9),
        "SMA75": LineStyle("SMA75", "#8e24aa", 1.5),
        "SMA200": LineStyle("SMA200", "#e53935", 2.4),
    }

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        close = bars["Close"]
        return pd.DataFrame({f"SMA{p}": sma(close, p) for p in self.PERIODS}, index=bars.index)


class BollingerBands(Indicator):
    """ボリンジャーバンド(20 本・±2σ)。終値で計算する。価格チャートに重ねる。

    スクイーズの判定は bollinger_squeeze() を使う。
    """

    name = "BB 20/2σ"
    PERIOD = 20
    K = 2.0
    # 色は TradingView の BB の初期設定に合わせる(+2σ は赤、-2σ は緑、その間を薄い青で塗る)。
    # 上昇・下落の意味の色ではないので、market_colors() には従わない
    lines = {
        "BB_Upper": LineStyle("BB +2σ", "#f23645", 0.9),
        "BB_Middle": LineStyle("BB 20", "#fb8c00", 0.8),
        "BB_Lower": LineStyle("BB -2σ", "#089981", 0.9),
    }
    fills = (FillStyle("BB_Upper", "BB_Lower", "#2196f3", 0.08),)

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        bands = bollinger(bars["Close"], self.PERIOD, self.K)
        return pd.DataFrame({"BB_Upper": bands["Upper"], "BB_Middle": bands["Middle"], "BB_Lower": bands["Lower"]},
                            index=bars.index)


class VolumeMA(Indicator):
    """出来高の移動平均(20 本)。出来高(Volume)で計算する。gui_chart.py では出来高の段に重ねる。

    急増率(その日の出来高 ÷ 前日までの 20 本の平均)は surge() で出す。ブレイクアウトの日に
    1.5 〜 2 倍以上あれば、出来高を伴った抜けと見る目安になる。
    """

    name = "出来高 MA20"
    PERIOD = 20
    lines = {"VOL_MA20": LineStyle("出来高 MA20", "#546e7a", 1.0)}

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"VOL_MA20": volume_ma(bars["Volume"], self.PERIOD)}, index=bars.index)

    def surge(self, bars: pd.DataFrame) -> pd.Series:
        """出来高の急増率(倍)。"""
        return volume_surge(bars["Volume"], self.PERIOD)
