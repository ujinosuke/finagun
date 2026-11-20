"""オシレーターの基本クラスとその種類。

オシレーターは、価格チャートとは別の枠(gui_chart.py では下段)に表示するインジケーター。
core_indicators.Indicator を継承し、計算(compute)と線の見た目(lines)の決まりは同じ。
それに加えて、縦軸の範囲(ylim)と基準線(levels)を持つ。

新しいオシレーターの作り方:
    1. Oscillator を継承し、name・lines・ylim・levels を決めて compute() を書く
       (棒で描く列は histograms に入れる)
    2. 下の OSCILLATORS に登録する(gui_chart.py の選択肢に出る)
"""

import numpy as np
import pandas as pd

from core_indicators import Indicator, LineStyle, atr, macd, rma, sma  # rma は他のファイルもここから使う


class Oscillator(Indicator):
    """別枠に表示するインジケーターの基本クラス。"""

    # 縦軸の範囲。None なら表示中の値に合わせる
    ylim: tuple[float, float] | None = None
    # 基準線(例: RSI の 30 と 70)。2 本あれば、その間を薄く塗る
    levels: tuple[float, ...] = ()
    # 線ではなく棒で描く列(例: MACD のヒストグラム)。0 より上は上昇の色、下は下落の色
    histograms: tuple[str, ...] = ()
    # 用語の説明のキー(core_glossary.py)。なければ ""
    term: str = ""


class RSI(Oscillator):
    """RSI(相対力指数)。期間は 14 本で固定。

    0 - 100 の値をとり、70 以上は買われすぎ、30 以下は売られすぎの目安とされる。
    値上がり幅と値下がり幅を RMA で平均して計算する(TradingView の RSI と同じ)。
    RSI の単純移動平均(14 本)も一緒に出す(TradingView の「RSI-based MA」)。
    線の色は TradingView の初期設定に合わせ、RSI は紫、移動平均は黄色。
    """

    name = "RSI"
    term = "rsi"
    PERIOD = 14
    MA_PERIOD = 14
    lines = {
        "RSI": LineStyle("RSI 14", "#7e57c2", 1.0),
        "RSI_SMA14": LineStyle("SMA 14", "#f7b924", 1.0),
    }
    ylim = (0, 100)
    levels = (30, 70)

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        change = bars["Close"].diff()
        gain = rma(change.clip(lower=0), self.PERIOD)
        loss = rma(-change.clip(upper=0), self.PERIOD)
        # 値下がりがなければ 100、値上がりがなければ 0
        rsi = np.where(loss == 0, 100, np.where(gain == 0, 0, 100 - 100 / (1 + gain / loss)))
        rsi = pd.Series(rsi, index=bars.index).where(gain.notna())
        return pd.DataFrame({"RSI": rsi, "RSI_SMA14": sma(rsi, self.MA_PERIOD)})


class MACD(Oscillator):
    """MACD(12 / 26 / 9)。終値で計算する。TradingView の ta.macd と同じ。

    MACD(短期 EMA - 長期 EMA)が Signal を上に抜ければ上昇の勢い、下に抜ければ下降の勢いの目安。
    ヒストグラム(MACD - Signal)は棒で描く。線の色は TradingView の初期設定に合わせる。
    """

    name = "MACD"
    term = "macd"
    FAST, SLOW, SIGNAL = 12, 26, 9
    lines = {
        "MACD_Hist": LineStyle("Hist", "#9e9e9e", 1.0),
        "MACD": LineStyle("MACD 12/26", "#2962ff", 1.0),
        "MACD_Signal": LineStyle("Signal 9", "#ff6d00", 1.0),
    }
    levels = (0,)
    histograms = ("MACD_Hist",)

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        result = macd(bars["Close"], self.FAST, self.SLOW, self.SIGNAL)
        return pd.DataFrame({"MACD_Hist": result["Hist"], "MACD": result["MACD"], "MACD_Signal": result["Signal"]},
                            index=bars.index)


class ATR(Oscillator):
    """ATR(14 本)。値幅の大きさ。スイングの損切り幅の目安(例: 買値 - 2 × ATR)。

    例外: 定義どおり高値・安値・終値を使う。TradingView の ta.atr と同じ。
    """

    name = "ATR"
    term = "atr"
    PERIOD = 14
    lines = {"ATR14": LineStyle("ATR 14", "#d81b60", 1.0)}

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"ATR14": atr(bars, self.PERIOD)}, index=bars.index)


# gui_chart.py で選べるオシレーター(表示名 -> クラス)。先頭が最初に選ばれる
OSCILLATORS: dict[str, type[Oscillator]] = {
    "RSI": RSI,
    "MACD": MACD,
    "ATR": ATR,
}
