"""インジケーターの基本クラスとその種類。

インジケーターは「計算」と「線の見た目」だけを受け持ち、描画は gui_chart.py などの表示側が行う。
バックテスト(fina_bktest.py)やストラテジー(core_strategies.py)からも、compute() で値だけを使える。

新しいインジケーターの作り方:
    1. Indicator を継承する
    2. name と lines(列名 -> 線の見た目)を決める
    3. compute() で、足と同じ行・lines と同じ列の DataFrame を返す

インジケーターは、渡された足(日足・週足・月足)の 1 本を 1 期間として計算する。
たとえば週足に SMA20 を使えば 20 週の移動平均になる。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class LineStyle:
    """インジケーターの線 1 本の見た目。"""
    label: str
    color: str
    width: float


class Indicator(ABC):
    """インジケーターの基本クラス。

    bars は Open / High / Low / Close / Volume の列を持ち、日付が行の DataFrame。
    """

    name: str = ""
    # 列名 -> 線の見た目。compute() が返す列と一致させる
    lines: dict[str, LineStyle] = {}

    @abstractmethod
    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        """bars と同じ行を持ち、lines の列名を列とする DataFrame を返す。

        計算に必要な本数がそろわない最初の行は NaN にする。
        """


def sma(series: pd.Series, period: int) -> pd.Series:
    """単純移動平均(SMA)。最初の period - 1 本は NaN になる。"""
    return series.rolling(period, min_periods=period).mean()


class SMAEssential3(Indicator):
    """基本の 3 本の単純移動平均線(20 / 75 / 200)。終値で計算する。

    20 : 緑の細い線
    75 : 紫の中くらいの線
    200: 赤の太い線
    """

    name = "SMA 20/75/200"
    PERIODS = (20, 75, 200)
    lines = {
        "SMA20": LineStyle("SMA20", "#43a047", 0.9),
        "SMA75": LineStyle("SMA75", "#8e24aa", 1.5),
        "SMA200": LineStyle("SMA200", "#e53935", 2.4),
    }

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        close = bars["Close"]
        return pd.DataFrame({f"SMA{p}": sma(close, p) for p in self.PERIODS}, index=bars.index)
