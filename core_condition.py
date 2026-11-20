"""状態(コンディション)の基本クラスとその種類。

コンディションは「その足で、ある状態が成り立っているか」を判定する。
インジケーターやオシレーターが数値を返すのに対し、コンディションは状態ごとの真偽を返す。
gui_chart.py では最下段に色で示し、バックテストやストラテジーでは売買の条件に使える。

新しいコンディションの作り方:
    1. Condition を継承し、name と states(状態の名前 -> 表示の見た目)を決める
       パラメータを持つときは params(core_indicators.Param の並び)を宣言する。
       値はコンストラクタ引数で受け取り(省略すればクラスの既定値)、self.values で読む。
       name は base_name とパラメータの値から自動で作る(例: 「SMAクロス(5,25)」)
    2. compute() で、足と同じ行・states と同じ列(True / False)の DataFrame を返す。
       説明を出したいときは "note" 列(文字列)に、判定の根拠を数値で書く
    3. 判定の根拠をチャートに線で示したいときは、links() も書く
    4. 下の CONDITIONS に、パラメータを決めたインスタンスを登録する(gui_chart.py の選択肢に出る)
       パラメータ違いは別の項目として並べる(プリセット方式)

注意: compute() の結果は、その足の時点で分かることだけを使う(未来の足を見ない)。
バックテストで使ったときに、実際には知り得ない情報で売買したことにならないようにするため。
指標の値は core_indicators などの関数で計算し、ここでは判定だけをする(計算を二重に書かない)。
設計は core-condition-design.md。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core_indicators import Param, resolve_params, sma
from core_oscillators import RSI
from core_settings import market_colors


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
    # パラメータの仕様。ないコンディションは空
    params: tuple[Param, ...] = ()
    # パラメータを持つとき、name の「(」より前の部分
    base_name: str = ""
    # 用語の説明のキー(core_glossary.py。画面でマウスを乗せると説明が出る)。なければ ""
    term: str = ""

    @classmethod
    def key(cls) -> str:
        """種類の名前(CONDITION_TYPES のキー。保存するときの識別子)。パラメータを持つなら base_name、なければ name。"""
        return cls.base_name or cls.name

    def __init__(self, **values):
        """パラメータを受け取る。指定のない項目はクラスの既定値(使う側の指定 > クラスの既定値)。"""
        self.values = resolve_params(self.params, values)
        if self.params:
            self.name = f"{self.base_name}({','.join(str(v) for v in self.values.values())})"

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
    term = "rsi_divergence"
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


def _up_down_styles(up: str, down: str) -> dict[str, StateStyle]:
    """上昇・下落の 2 つの状態の見た目。色は全体設定の上昇・下落の色(market_colors)に従う。"""
    up_color, down_color = market_colors()
    return {"up": StateStyle(up, up_color), "down": StateStyle(down, down_color)}


def _crossed(a: pd.Series, b: pd.Series) -> tuple[pd.Series, pd.Series]:
    """a が b を上に抜けた足・下に抜けた足。

    上抜け: 前の足で a ≦ b、その足で a > b。下抜け: 前の足で a ≧ b、その足で a < b。
    どちらかの値がない(NaN の)足では抜けたことにしない。
    """
    prev_a, prev_b = a.shift(1), b.shift(1)
    known = a.notna() & b.notna() & prev_a.notna() & prev_b.notna()
    return known & (prev_a <= prev_b) & (a > b), known & (prev_a >= prev_b) & (a < b)


class SMACross(Condition):
    """SMA のクロス(イベント型。起きた足 1 本だけ True)。終値の SMA で判定する。

    ゴールデン: 短期 SMA が長期 SMA を下から上に抜けた足(前の足で 短期 ≦ 長期、その足で 短期 > 長期)
    デッド    : 短期 SMA が長期 SMA を上から下に抜けた足(前の足で 短期 ≧ 長期、その足で 短期 < 長期)
    """

    base_name = "SMAクロス"
    term = "sma_cross"
    params = (
        Param("fast", "短期", 5, 2, 400),
        Param("slow", "長期", 25, 3, 400),
    )

    def __init__(self, **values):
        super().__init__(**values)
        if self.values["fast"] >= self.values["slow"]:
            raise ValueError(f"短期は長期より短くしてください: {self.values}")
        self.states = _up_down_styles("ゴールデン", "デッド")

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        fast_n, slow_n = self.values["fast"], self.values["slow"]
        fast, slow = sma(bars["Close"], fast_n), sma(bars["Close"], slow_n)
        golden, dead = _crossed(fast, slow)
        result = pd.DataFrame({"up": golden, "down": dead, "note": ""}, index=bars.index)
        for mask, word in ((golden, "上抜け"), (dead, "下抜け")):
            rows = np.flatnonzero(mask.to_numpy())
            result.iloc[rows, result.columns.get_loc("note")] = [
                f"SMA{fast_n} {fast.iloc[i]:,.1f} が SMA{slow_n} {slow.iloc[i]:,.1f} を{word}"
                f"(前の足 {fast.iloc[i - 1]:,.1f} / {slow.iloc[i - 1]:,.1f})"
                for i in rows
            ]
        return result


class SMAPerfectOrder(Condition):
    """SMA のパーフェクトオーダー(状態型。続いている間 True)。終値の SMA で判定する。

    上昇: 短い順に SMA が大きい(例: SMA5 > SMA25 > SMA75 > SMA200)
    下降: 短い順に SMA が小さい(例: SMA5 < SMA25 < SMA75 < SMA200)
    いちばん長い SMA が計算できるまで(SMA200 なら最初の 199 本)は、どちらでもない。
    """

    base_name = "パーフェクトオーダー"
    term = "perfect_order"
    params = (
        Param("p1", "期間1", 5, 2, 400),
        Param("p2", "期間2", 25, 3, 400),
        Param("p3", "期間3", 75, 4, 400),
        Param("p4", "期間4", 200, 5, 400),
    )

    def __init__(self, **values):
        super().__init__(**values)
        periods = list(self.values.values())
        if any(a >= b for a, b in zip(periods, periods[1:])):
            raise ValueError(f"期間は短い順に並べてください: {self.values}")
        self.states = _up_down_styles("上昇", "下降")

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        periods = list(self.values.values())
        lines = [sma(bars["Close"], p) for p in periods]
        known = pd.concat(lines, axis=1).notna().all(axis=1)
        up, down = known.copy(), known.copy()
        for short, long in zip(lines, lines[1:]):
            up &= short > long
            down &= short < long
        result = pd.DataFrame({"up": up, "down": down, "note": ""}, index=bars.index)
        for mask, sign in ((up, " > "), (down, " < ")):
            rows = np.flatnonzero(mask.to_numpy())
            result.iloc[rows, result.columns.get_loc("note")] = [
                sign.join(f"SMA{p} {line.iloc[i]:,.1f}" for p, line in zip(periods, lines)) for i in rows
            ]
        return result


class PriceVsSMA(Condition):
    """終値と SMA の位置(状態型。続いている間 True)。終値で判定する。

    上: 終値が SMA より上(終値 > SMA)
    下: 終値が SMA より下(終値 < SMA)
    """

    base_name = "終値とSMA"
    term = "price_vs_sma"
    params = (Param("period", "期間", 25, 2, 400),)

    def __init__(self, **values):
        super().__init__(**values)
        self.states = _up_down_styles("上", "下")

    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        period = self.values["period"]
        close = bars["Close"]
        line = sma(close, period)
        known = line.notna()
        above, below = known & (close > line), known & (close < line)
        result = pd.DataFrame({"up": above, "down": below, "note": ""}, index=bars.index)
        for mask in (above, below):
            rows = np.flatnonzero(mask.to_numpy())
            result.iloc[rows, result.columns.get_loc("note")] = [
                f"終値 {close.iloc[i]:,.1f}(SMA{period} {line.iloc[i]:,.1f} から "
                f"{(close.iloc[i] / line.iloc[i] - 1) * 100:+.1f}%)"
                for i in rows
            ]
        return result


# コンディションの種類(種類の名前 -> クラス)。スクリーニングの条件の追加で選べる一覧(条件カタログ)。
# 条件セット・ストラテジーの保存では、種類の名前とパラメータの組でコンディションを表す
CONDITION_TYPES: dict[str, type[Condition]] = {
    cls.key(): cls for cls in (RSIDivergence, SMACross, SMAPerfectOrder, PriceVsSMA)
}


def _presets(*conditions: Condition) -> dict[str, Condition]:
    """パラメータを決めたコンディションを、名前(例: 「SMAクロス(5,25)」)で引けるようにする。"""
    registry = {condition.name: condition for condition in conditions}
    assert len(registry) == len(conditions), "同じ名前のプリセットがあります"
    return registry


# gui_chart.py・fina_screening.py で使うコンディション(表示名 -> パラメータを決めたインスタンス)。
# パラメータ違いは別の項目として並べる(プリセット方式)。先頭が最初に選ばれる
CONDITIONS: dict[str, Condition] = _presets(
    RSIDivergence(),
    SMACross(fast=5, slow=25),
    SMACross(fast=25, slow=75),
    SMAPerfectOrder(),
    PriceVsSMA(period=25),
)
