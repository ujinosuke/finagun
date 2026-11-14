"""状態(コンディション)の基本クラスとその種類。

コンディションは「その足で、ある状態が成り立っているか」を判定する。
インジケーターやオシレーターが数値を返すのに対し、コンディションは状態ごとの真偽を返す。
fina_chart.py では最下段に色で示し、バックテストやストラテジーでは売買の条件に使える。

新しいコンディションの作り方:
    1. Condition を継承し、name と states(状態の名前 -> 表示の見た目)を決める
    2. compute() で、足と同じ行・states と同じ列(True / False)の DataFrame を返す。
       説明を出したいときは "note" 列(文字列)も付ける
    3. 判定の根拠をチャートに線で示したいときは、links() も書く
    4. 下の CONDITIONS に登録する(fina_chart.py の選択肢に出る)

注意: compute() の結果は、その足の時点で分かることだけを使う(未来の足を見ない)。
バックテストで使ったときに、実際には知り得ない情報で売買したことにならないようにするため。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

from fina_oscillators import RSI


@dataclass(frozen=True)
class StateStyle:
    """状態 1 つの表示の見た目。"""
    label: str
    color: str


@dataclass(frozen=True)
class Link:
    """判定の根拠を示す線(2 点を結ぶ)。

    pane は線を描く場所。"price" なら価格チャート、それ以外はオシレーターの名前(例: "RSI")で、
    そのオシレーターを表示しているときだけ描く。
    """
    state: str                         # どの状態の根拠か(線の色に使う)
    pane: str
    start: tuple[pd.Timestamp, float]  # (日付、値)
    end: tuple[pd.Timestamp, float]


class Condition(ABC):
    """コンディションの基本クラス。

    bars は Open / High / Low / Close / Volume の列を持ち、日付が行の DataFrame。
    """

    name: str = ""
    # 状態の名前 -> 表示の見た目。compute() が返す列と一致させる
    states: dict[str, StateStyle] = {}

    @abstractmethod
    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        """bars と同じ行を持ち、states の名前を列(True / False)とする DataFrame を返す。

        状態の説明として "note" 列(文字列、なければ空文字)を付けてもよい。
        """

    def links(self, bars: pd.DataFrame) -> list[Link]:
        """判定の根拠としてチャートに描く線。描くものがなければ空のリスト。"""
        return []


def pivots(series: pd.Series, left: int, right: int, find_high: bool) -> list[int]:
    """山(find_high=True)または谷の位置(行番号)を返す。

    左 left 本・右 right 本の範囲で、ただ 1 つの最高値(最安値)になっている足を山(谷)とする。
    右 right 本が出そろうまで確定しないので、山や谷が分かるのは right 本後になる。
    """
    values = series.to_numpy(dtype=float)
    found = []
    for i in range(left, len(values) - right):
        window = values[i - left: i + right + 1]
        if np.isnan(window).any():
            continue
        extreme = window.max() if find_high else window.min()
        if values[i] == extreme and np.count_nonzero(window == extreme) == 1:
            found.append(i)
    return found


@dataclass(frozen=True)
class _Divergence:
    """見つかったダイバージェンス 1 つ。prev と now は 2 つの谷(山)の行番号。"""
    state: str
    find_high: bool
    prev: int
    now: int


class RSIDivergence(Condition):
    """RSI のダイバージェンス(株価と RSI の逆行)。

    強気: 株価の安値は前回の谷より下がったのに、RSI の谷は前回より上がっている
          (下げの勢いが弱まっている。上昇に転じる兆しとされる)
    弱気: 株価の高値は前回の山より上がったのに、RSI の山は前回より下がっている
          (上げの勢いが弱まっている。下落に転じる兆しとされる)

    判定方法は TradingView の「RSI Divergence Indicator」と同じ:
        1. RSI の谷(山)を探す。左右 5 本より低い(高い)足を谷(山)とする
        2. 前回の谷(山)との間隔が 5 - 60 本なら、2 つの谷(山)で RSI と株価を比べる
           株価は、その足の安値(谷のとき)と高値(山のとき)を使う
        3. 谷(山)は右 5 本が出そろって確定するので、状態はその確定した足に付ける

    links() は、比べた 2 つの谷(山)を価格チャートと RSI の上で結ぶ線を返す。
    """

    name = "RSIダイバージェンス"
    states = {
        "bullish": StateStyle("強気", "#26a69a"),
        "bearish": StateStyle("弱気", "#ef5350"),
    }
    LEFT = 5          # 谷・山の判定に使う左側の本数
    RIGHT = 5         # 谷・山の判定に使う右側の本数(この本数だけ確定が遅れる)
    RANGE_MIN = 5     # 前回の谷・山との間隔の最小
    RANGE_MAX = 60    # 前回の谷・山との間隔の最大

    @staticmethod
    def _price(bars: pd.DataFrame, find_high: bool) -> pd.Series:
        """比べる株価。山なら高値、谷なら安値。"""
        return bars["High" if find_high else "Low"]

    def _find(self, bars: pd.DataFrame) -> tuple[pd.Series, list[_Divergence]]:
        """RSI と、見つかったダイバージェンスの一覧を返す。"""
        rsi = RSI().compute(bars)["RSI"]
        found = []
        for state, find_high in (("bullish", False), ("bearish", True)):
            price = self._price(bars, find_high)
            points = pivots(rsi, self.LEFT, self.RIGHT, find_high)
            for prev, now in zip(points, points[1:]):
                if not self.RANGE_MIN <= now - prev <= self.RANGE_MAX:
                    continue
                rsi_up = rsi.iloc[now] > rsi.iloc[prev]
                price_up = price.iloc[now] > price.iloc[prev]
                rsi_down = rsi.iloc[now] < rsi.iloc[prev]
                price_down = price.iloc[now] < price.iloc[prev]
                # 強気: 株価は下がり RSI は上がる / 弱気: 株価は上がり RSI は下がる
                if (rsi_down and price_up) if find_high else (rsi_up and price_down):
                    found.append(_Divergence(state, find_high, prev, now))
        return rsi, found

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        rsi, found = self._find(bars)
        result = pd.DataFrame({"bullish": False, "bearish": False, "note": ""}, index=bars.index)
        dates = bars.index
        for d in found:
            price = self._price(bars, d.find_high)
            kind = "山" if d.find_high else "谷"
            confirmed = d.now + self.RIGHT
            result.iloc[confirmed, result.columns.get_loc(d.state)] = True
            result.iloc[confirmed, result.columns.get_loc("note")] = (
                f"{kind} {dates[d.prev]:%Y-%m-%d} → {dates[d.now]:%Y-%m-%d}"
                f"(株価 {price.iloc[d.prev]:,.1f} → {price.iloc[d.now]:,.1f}、"
                f"RSI {rsi.iloc[d.prev]:.1f} → {rsi.iloc[d.now]:.1f})"
            )
        return result

    def links(self, bars: pd.DataFrame) -> list[Link]:
        rsi, found = self._find(bars)
        dates = bars.index
        lines = []
        for d in found:
            price = self._price(bars, d.find_high)
            lines.append(Link(
                d.state, "price",
                (dates[d.prev], price.iloc[d.prev]), (dates[d.now], price.iloc[d.now]),
            ))
            lines.append(Link(
                d.state, RSI.name,
                (dates[d.prev], rsi.iloc[d.prev]), (dates[d.now], rsi.iloc[d.now]),
            ))
        return lines


# fina_chart.py で選べるコンディション(表示名 -> クラス)。先頭が最初に選ばれる
CONDITIONS: dict[str, type[Condition]] = {
    "RSIダイバージェンス": RSIDivergence,
}
