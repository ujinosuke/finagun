"""オシレーターの基本クラスとその種類。

オシレーターは、価格チャートとは別の枠(gui_chart.py では下段)に表示するインジケーター。
core_indicators.Indicator を継承し、計算(compute)と線の見た目(lines)の決まりは同じ。
それに加えて、縦軸の範囲(ylim)と基準線(levels)を持つ。

新しいオシレーターの作り方:
    1. Oscillator を継承し、name・lines・ylim・levels を決めて compute() を書く
    2. 下の OSCILLATORS に登録する(gui_chart.py の選択肢に出る)
"""

import numpy as np
import pandas as pd

from core_indicators import Indicator, LineStyle, sma


class Oscillator(Indicator):
    """別枠に表示するインジケーターの基本クラス。"""

    # 縦軸の範囲。None なら表示中の値に合わせる
    ylim: tuple[float, float] | None = None
    # 基準線(例: RSI の 30 と 70)。2 本あれば、その間を薄く塗る
    levels: tuple[float, ...] = ()


def rma(series: pd.Series, period: int) -> pd.Series:
    """ワイルダーの移動平均(RMA)。RSI などで使う。

    最初の値は先頭 period 本の単純平均で、以降は
    「前回の値 × (period - 1) / period + 今回の値 / period」で更新する。
    TradingView の ta.rma と同じ計算。
    """
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if valid.size:
        seed = valid[0] + period - 1
        if seed < len(values):
            out[seed] = values[valid[0]: seed + 1].mean()
            for i in range(seed + 1, len(values)):
                out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return pd.Series(out, index=series.index)


class RSI(Oscillator):
    """RSI(相対力指数)。期間は 14 本で固定。

    0 - 100 の値をとり、70 以上は買われすぎ、30 以下は売られすぎの目安とされる。
    値上がり幅と値下がり幅を RMA で平均して計算する(TradingView の RSI と同じ)。
    RSI の単純移動平均(14 本)も一緒に出す(TradingView の「RSI-based MA」)。
    線の色は TradingView の初期設定に合わせ、RSI は紫、移動平均は黄色。
    """

    name = "RSI"
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


# gui_chart.py で選べるオシレーター(表示名 -> クラス)。先頭が最初に選ばれる
OSCILLATORS: dict[str, type[Oscillator]] = {
    "RSI": RSI,
}
