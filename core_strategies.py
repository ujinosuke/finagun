"""ストラテジー(売買戦略)と条件セット。

設計は pico_strategy-screening-design.md。層は次のとおり:
    core_indicators / core_oscillators   値を出す(SMA・MACD・RSI など)
    core_condition                       値を読んで状態を判定する(クロス・パーフェクトオーダーなど)
    core_strategies(このファイル)       条件の束(条件セット)+ 出口ルール = ストラテジー
    fina_screening / fina_bktest         条件セット・ストラテジーを使う画面

条件セット(ConditionSet)は、条件を 2 層で組み合わせる:
    シグナル層(OR)        買いの合図になる条件。どれか 1 つが成り立てば候補
    フィルター層(AND+NOT)  環境の条件。すべて成り立つこと。「除外」の行は、成り立ったら見送る
    合成式: (シグナルのどれか) AND (フィルターのすべて) AND NOT (除外のどれか)
    スクリーニングでは、シグナルは「直近 N 本以内に成り立った」で拾い(新鮮さ)、フィルターは最新の足で調べる。
    バックテストでは、シグナルとフィルターを同じ足で調べる。

ストラテジー(StrategyDef)は「名前・説明・買い条件(条件セット)・出口ルール(ExitRule)」の定義。
RuleStrategy が定義を Strategy として動かし、スクリーニングとバックテストで同じ定義を使う。
出口ルールは部品を選んで組み合わせる:
    利確      固定% / 直近高値 / なし
    損切り    固定% / リスクリワード(利幅から逆算) / ATR / なし
    その他    日数(○本たったら終値で売る)、条件による決済(条件が成り立ったら売る)
    利確・損切りの値が決まらない合図(直近高値がない、利幅が小さすぎるなど)は見送る

Strategy の役目(基本クラス。コードで書くストラテジーもこれを継承する):
    entries()  足を最初から最後まで調べ、買いの合図(Entry)の一覧を返す。
               Entry には、買う日・買値・利確の値(take_profit)・損切りの値(stop_loss)を入れる
    exit_on()  保有中の足 1 本ごとに呼ばれ、売るなら Exit を返す。
               初期の実装は「終値が take_profit 以上なら利確、stop_loss 以下なら損切り」
    trades()   1 銘柄について「買い → 売り」を 1 つずつ順に進め、取引(Trade)の一覧を返す。
               保有中に出た買いの合図は使わない(同時に持つのは 1 つだけ)

約束ごと(CLAUDE.md も参照):
    - 買いのみ(レバレッジなし)。売りから入る取引は扱わない
    - 断りがなければ、株価は終値(Close)だけを使う。売買も、判定した足の終値で行ったことにする
      (例外: ATR の損切りは、定義どおり高値・安値・終値で ATR を計算する)
    - trades() の fill で約定の値を選べる(FILL_MODES)。
        終値      : ストラテジーが決めた値(判定した足の終値)で約定する
        翌日の始値: 買い・売りを判定した足の次の足の始値で約定する。
                    判定(entries・exit_on)は終値のまま変えず、約定の日と値だけを 1 本後ろへずらす
    - その足の時点で分かることだけを使う(未来の足を見ない)。
      山や谷のように、後の足が出そろって確定するものは、確定した足から使う

保存の形(JSON。条件は「種類の名前・状態・パラメータ」で表す):
    条件セット {"name": "...", "signals": [{"condition": "SMAクロス", "state": "up", "params": {"fast": 5, "slow": 25}}],
                "filters": [{"condition": "終値とSMA", "state": "down", "params": {"period": 75}, "exclude": true}],
                "freshness_days": 5}
    利用者が保存した条件セット(スクリーニングのユーザープリセット)は screening_presets.json に置く。

使い方(動作の確認):
    python core_strategies.py                 # 登録されているストラテジーの一覧
    python core_strategies.py 7203            # 7203 で全ストラテジーの取引を表示
    python core_strategies.py 7203 <名前>     # ストラテジーを指定
"""

import json
import math
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from core_condition import CONDITION_TYPES, Condition, pivots
from core_gather import BASE_DIR, csv_path_for, load_saved
from core_indicators import Param, atr, resolve_params


# 約定の値の選び方(名前 -> 表示名)
FILL_CLOSE = "close"
FILL_NEXT_OPEN = "next_open"
FILL_MODES = {
    FILL_CLOSE: "終値",
    FILL_NEXT_OPEN: "翌日の始値",
}

# シグナルの新鮮さ(直近何本以内に成り立ったシグナルを拾うか)の既定。設計の決め残し。仮に 5 本(1 週間)
DEFAULT_FRESHNESS = 5
MAX_FRESHNESS = 250

# 利用者が保存した条件セット(スクリーニングのユーザープリセット)
PRESETS_PATH = BASE_DIR / "screening_presets.json"
PRESETS_VERSION = 1


# ---- 売買の記録 ----

@dataclass(frozen=True)
class Entry:
    """買いの合図 1 つ。"""
    index: int            # 買う足の行番号
    date: pd.Timestamp    # 買う日
    price: float          # 買値(初期の実装ではその日の終値)
    take_profit: float    # 利確の値。終値がこの値以上になったら売る(math.inf なら利確しない)
    stop_loss: float      # 損切りの値。終値がこの値以下になったら売る(0 以下なら損切りしない)
    note: str = ""        # 買った理由など(表示用)


@dataclass(frozen=True)
class Exit:
    """売り 1 つ。"""
    index: int            # 売る足の行番号
    date: pd.Timestamp
    price: float
    reason: str           # 「利確」「損切り」など(表示用)


@dataclass(frozen=True)
class Trade:
    """取引 1 つ(買いと売りの組)。exit が None なら、データの最後の足でまだ保有中。"""
    entry: Entry
    exit: Exit | None

    @property
    def return_pct(self) -> float:
        """損益率(%)。保有中なら NaN。手数料は含まない(fina_bktest.py で扱う)。"""
        if self.exit is None:
            return math.nan
        return (self.exit.price / self.entry.price - 1) * 100

    @property
    def bars_held(self) -> int | None:
        """保有した足の本数。保有中なら None。"""
        return None if self.exit is None else self.exit.index - self.entry.index


# ---- ストラテジーの基本クラス ----

class Strategy(ABC):
    """ストラテジーの基本クラス。

    bars は Open / High / Low / Close / Volume の列を持ち、日付が行の DataFrame(古い順)。
    """

    name: str = ""         # 短い名前(一覧やコマンドでの指定に使う)
    description: str = ""  # 売買のルールの説明(1〜数行)

    @abstractmethod
    def entries(self, bars: pd.DataFrame) -> list[Entry]:
        """買いの合図の一覧を、日付の古い順に返す。

        i 本目の合図は、0〜i 本目の足だけから決めること(未来の足を見ない)。
        """

    def exit_on(self, entry: Entry, bars: pd.DataFrame, i: int) -> Exit | None:
        """保有中の i 本目の足(買った足より後)で売るなら Exit、売らないなら None を返す。

        初期の実装: 終値が take_profit 以上なら利確、stop_loss 以下なら損切り。どちらもその日の終値で売る。
        """
        close = bars["Close"].iloc[i]
        if math.isnan(close):
            return None
        if close >= entry.take_profit:
            return Exit(i, bars.index[i], close, "利確")
        if close <= entry.stop_loss:
            return Exit(i, bars.index[i], close, "損切り")
        return None

    def trades(self, bars: pd.DataFrame, start: pd.Timestamp | None = None, fill: str = FILL_CLOSE) -> list[Trade]:
        """1 銘柄の取引の一覧。同時に持つのは 1 つだけで、保有中に出た買いの合図は使わない。

        start を渡すと、その日より前の買いの合図は使わない(バックテストの期間の始まり)。
        インジケーターは start より前の足も使って計算するので、期間の初日から合図を出せる。
        fill は約定の値(FILL_MODES)。FILL_NEXT_OPEN なら、判定した足の次の足の始値で買い・売りする。
            最後の足で出た買いの合図は、次の足がないので使わない。
            最後の足で出た売りの合図は、次の足がないので保有中(exit が None)のままにする。
        """
        if fill not in FILL_MODES:
            raise ValueError(f"約定の値の指定が正しくありません: {fill}")
        result = []
        free_from = 0  # この行番号以降の合図なら買える(判定した足の行番号で比べる)
        last = len(bars) - 1
        for entry in self.entries(bars):
            if entry.index < free_from or (start is not None and entry.date < start):
                continue
            if fill == FILL_NEXT_OPEN and entry.index >= last:
                break  # 次の足がないので買えない
            exit_ = None
            for i in range(entry.index + 1, len(bars)):
                exit_ = self.exit_on(entry, bars, i)  # 判定は、ストラテジーが決めた買い(終値)のまま行う
                if exit_ is not None:
                    break
            if fill == FILL_NEXT_OPEN:
                filled_entry = replace(entry, index=entry.index + 1, date=bars.index[entry.index + 1],
                                       price=next_open(bars, entry.index))
                filled_exit = None
                if exit_ is not None and exit_.index < last:
                    filled_exit = replace(exit_, index=exit_.index + 1, date=bars.index[exit_.index + 1],
                                          price=next_open(bars, exit_.index))
                result.append(Trade(filled_entry, filled_exit))
                if filled_exit is None:  # データの最後まで保有中
                    break
            else:
                result.append(Trade(entry, exit_))
                if exit_ is None:  # データの最後まで保有中
                    break
            free_from = exit_.index + 1  # 売りを判定した足の次の足から、また買える
        return result


def next_open(bars: pd.DataFrame, i: int) -> float:
    """i 本目の次の足の始値。始値がなければ、その足の終値。"""
    value = bars["Open"].iloc[i + 1]
    return float(bars["Close"].iloc[i + 1] if math.isnan(value) else value)


def recent_high_above(close: pd.Series, i: int, price: float, left: int, right: int, lookback: int):
    """i 本目の時点で確定している山(終値)のうち、price より高く、i に一番近いものを返す。

    山は左 left 本・右 right 本より高い足(core_condition.pivots と同じ)。
    右 right 本が出そろって確定するので、使えるのは i - right 本目までの山。
    lookback 本前より古い山は探さない。見つからなければ None。
    戻り値は (山の行番号, 山の終値)。
    """
    start = max(0, i - lookback)
    window = close.iloc[start: i + 1]  # i 本目までしか見ない
    for p in reversed(pivots(window, left, right, find_high=True)):
        value = window.iloc[p]
        if value > price:
            return start + p, value
    return None


# ---- 条件セット ----

@dataclass(frozen=True, eq=False)
class ConditionRow:
    """条件セットの 1 行。コンディションの種類・状態・パラメータ(と、フィルター層なら除外か)。

    condition は core_condition.CONDITION_TYPES のキー、state はそのコンディションの状態の名前。
    作るときにコンディションを組み立てて、種類・状態・パラメータが正しいかを確かめる(正しくなければ ValueError)。
    """
    condition: str
    state: str
    params: dict = field(default_factory=dict)
    exclude: bool = False  # フィルター層で「除外」(成り立ったら見送る)

    def __post_init__(self):
        if self.condition not in CONDITION_TYPES:
            raise ValueError(f"コンディションの種類がありません: {self.condition}")
        built = CONDITION_TYPES[self.condition](**self.params)  # パラメータが正しくなければ ValueError
        if self.state not in built.states:
            raise ValueError(f"{built.name} に状態「{self.state}」はありません")
        object.__setattr__(self, "params", dict(built.values))  # 指定のない項目も既定値で埋めて持つ
        object.__setattr__(self, "_built", built)

    @property
    def built(self) -> Condition:
        """パラメータを決めたコンディション。"""
        return self._built

    @property
    def state_label(self) -> str:
        return self.built.states[self.state].label

    @property
    def label(self) -> str:
        """文として読める表示(例: SMAクロス(5,25) の「ゴールデン」)。"""
        return f"{self.built.name} の「{self.state_label}」"

    def to_json(self, with_exclude: bool = False) -> dict:
        data = {"condition": self.condition, "state": self.state, "params": dict(self.params)}
        if with_exclude:
            data["exclude"] = self.exclude
        return data

    @classmethod
    def from_json(cls, data) -> "ConditionRow":
        if not isinstance(data, dict):
            raise ValueError(f"条件の形が違います: {data!r}")
        params = data.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"パラメータの形が違います: {params!r}")
        return cls(str(data.get("condition", "")), str(data.get("state", "")), params, bool(data.get("exclude", False)))


@dataclass(frozen=True)
class Evaluation:
    """条件セットを 1 銘柄の足で調べた結果(足と同じ行)。"""
    frames: list[pd.DataFrame]   # 条件の行ごとの compute() の結果(signals、filters の順)
    signal_hits: list[pd.Series] # シグナルの行ごとの真偽
    filter_ok: pd.Series         # フィルター層をすべて満たしているか(除外も含む)
    buy: pd.Series               # シグナルのどれか AND フィルター(同じ足で調べたもの。バックテストで使う)


@dataclass(frozen=True)
class ConditionSet:
    """条件セット(シグナル層 OR + フィルター層 AND+NOT)。"""
    name: str = ""
    signals: tuple[ConditionRow, ...] = ()
    filters: tuple[ConditionRow, ...] = ()
    freshness_days: int = DEFAULT_FRESHNESS  # スクリーニングで、直近何本以内のシグナルを拾うか

    def evaluate(self, bars: pd.DataFrame) -> Evaluation:
        """足の全体を調べる。同じコンディション(パラメータも同じ)は 1 回だけ計算する。"""
        computed: dict[str, pd.DataFrame] = {}

        def frame(row: ConditionRow) -> pd.DataFrame:
            name = row.built.name
            if name not in computed:
                computed[name] = row.built.compute(bars)
            return computed[name]

        frames = [frame(row) for row in (*self.signals, *self.filters)]
        signal_hits = [frame(row)[row.state].astype(bool) for row in self.signals]
        filter_ok = pd.Series(True, index=bars.index)
        for row in self.filters:
            state = frame(row)[row.state].astype(bool)
            filter_ok &= ~state if row.exclude else state
        if signal_hits:
            any_signal = pd.concat(signal_hits, axis=1).any(axis=1)
        else:
            any_signal = pd.Series(False, index=bars.index)
        return Evaluation(frames, signal_hits, filter_ok, any_signal & filter_ok)

    def describe(self) -> str:
        """1 行の説明(例: SMAクロス(5,25) の「ゴールデン」 / 条件: 終値とSMA(200) の「上」)。"""
        parts = [" または ".join(row.label for row in self.signals) or "シグナルなし"]
        musts = [row.label for row in self.filters if not row.exclude]
        nots = [row.label for row in self.filters if row.exclude]
        if musts:
            parts.append("条件: " + "、".join(musts))
        if nots:
            parts.append("除外: " + "、".join(nots))
        return " / ".join(parts)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "signals": [row.to_json() for row in self.signals],
            "filters": [row.to_json(with_exclude=True) for row in self.filters],
            "freshness_days": self.freshness_days,
        }

    @classmethod
    def from_json(cls, data) -> "ConditionSet":
        if not isinstance(data, dict):
            raise ValueError("条件セットの形が違います")
        freshness = data.get("freshness_days", DEFAULT_FRESHNESS)
        if isinstance(freshness, bool) or not isinstance(freshness, int) or not 1 <= freshness <= MAX_FRESHNESS:
            raise ValueError(f"新鮮さは 1 - {MAX_FRESHNESS} の整数で指定してください: {freshness!r}")
        return cls(
            str(data.get("name", "")),
            tuple(ConditionRow.from_json(row) for row in data.get("signals") or []),
            tuple(ConditionRow.from_json(row) for row in data.get("filters") or []),
            freshness,
        )


# ---- 出口ルールの部品 ----

class ExitPart:
    """出口ルールの部品の基本クラス。パラメータは Param で宣言し、コンストラクタ引数で受け取る。"""

    key: str = ""                  # 種類の名前(保存するときの識別子)
    params: tuple[Param, ...] = ()

    def __init__(self, **values):
        self.values = resolve_params(self.params, values)

    @property
    def name(self) -> str:
        """パラメータ込みの名前(例: 固定%(10.0))。"""
        if not self.params:
            return self.key
        return f"{self.key}({','.join(f'{v:g}' for v in self.values.values())})"

    def to_json(self) -> dict:
        return {"kind": self.key, "params": dict(self.values)}


class TakeProfitPart(ExitPart):
    """利確の決め方。"""

    def level(self, bars: pd.DataFrame, i: int, price: float) -> tuple[float, str] | None:
        """i 本目に price で買うときの (利確の値, 説明)。決まらなければ None(その合図は見送る)。"""
        raise NotImplementedError


class StopLossPart(ExitPart):
    """損切りの決め方。"""

    needs_take_profit = False  # 利確の値から決めるか(リスクリワード)

    def level(self, bars: pd.DataFrame, i: int, price: float, take_profit: float) -> tuple[float, str] | None:
        """i 本目に price で買うときの (損切りの値, 説明)。決まらなければ None(その合図は見送る)。"""
        raise NotImplementedError


class FixedTakeProfit(TakeProfitPart):
    """固定%: 買値の +pct% で利確する。"""
    key = "固定%"
    params = (Param("pct", "利確(+%)", 10.0, 0.1, 1000.0),)

    def level(self, bars, i, price):
        return price * (1 + self.values["pct"] / 100), f"利確 +{self.values['pct']:g}%"


class RecentHighTakeProfit(TakeProfitPart):
    """直近高値: 買う時点で確定している、買値より高い一番最近の山(終値)で利確する。

    山は左右 left / right 本より終値が高い足で、lookback 本前までさかのぼって探す。
    見つからない、または利幅が買値の min_reward% 未満なら、その合図は見送る(出口に由来する見送り)。
    """
    key = "直近高値"
    params = (
        Param("left", "山の左の本数", 5, 1, 50),
        Param("right", "山の右の本数", 5, 1, 50),
        Param("lookback", "さかのぼる本数", 250, 10, 2000),
        Param("min_reward", "利幅の下限(%)", 3.0, 0.0, 100.0),
    )

    def level(self, bars, i, price):
        v = self.values
        high = recent_high_above(bars["Close"], i, price, v["left"], v["right"], v["lookback"])
        if high is None:
            return None  # 直近高値がない
        high_index, value = high
        if value - price < price * v["min_reward"] / 100:
            return None  # 利幅が小さすぎる
        return value, f"直近高値 {bars.index[high_index]:%Y-%m-%d} {value:,.1f}"


class NoTakeProfit(TakeProfitPart):
    """なし: 利確しない(ほかの売り方で売る)。"""
    key = "なし"

    def level(self, bars, i, price):
        return math.inf, ""


class FixedStopLoss(StopLossPart):
    """固定%: 買値の -pct% で損切りする。"""
    key = "固定%"
    params = (Param("pct", "損切り(-%)", 5.0, 0.1, 99.0),)

    def level(self, bars, i, price, take_profit):
        return price * (1 - self.values["pct"] / 100), f"損切り -{self.values['pct']:g}%"


class RiskRewardStopLoss(StopLossPart):
    """リスクリワード: 利幅 ÷ ratio だけ買値から下で損切りする(ratio 2 なら利幅の半分)。"""
    key = "リスクリワード"
    params = (Param("ratio", "利幅÷損失幅", 2.0, 0.1, 20.0),)
    needs_take_profit = True

    def level(self, bars, i, price, take_profit):
        return price - (take_profit - price) / self.values["ratio"], f"リスクリワード {self.values['ratio']:g}"


class ATRStopLoss(StopLossPart):
    """ATR: 買値 - multiple × ATR(period) で損切りする。ATR が計算できない足の合図は見送る。

    例外: ATR は定義どおり高値・安値・終値で計算する(core_indicators.atr)。
    """
    key = "ATR"
    params = (
        Param("multiple", "ATR の倍数", 2.0, 0.1, 20.0),
        Param("period", "ATR の期間", 14, 2, 200),
    )

    def level(self, bars, i, price, take_profit):
        value = atr(bars.iloc[: i + 1], self.values["period"]).iloc[-1]  # i 本目までしか見ない
        if math.isnan(value):
            return None
        return price - self.values["multiple"] * value, f"ATR {value:,.1f} × {self.values['multiple']:g}"


class NoStopLoss(StopLossPart):
    """なし: 損切りしない(ほかの売り方で売る)。"""
    key = "なし"

    def level(self, bars, i, price, take_profit):
        return 0.0, ""


TAKE_PROFITS: dict[str, type[TakeProfitPart]] = {
    cls.key: cls for cls in (FixedTakeProfit, RecentHighTakeProfit, NoTakeProfit)
}
STOP_LOSSES: dict[str, type[StopLossPart]] = {
    cls.key: cls for cls in (FixedStopLoss, RiskRewardStopLoss, ATRStopLoss, NoStopLoss)
}


def _part_from_json(data, kinds: dict) -> ExitPart:
    if not isinstance(data, dict) or data.get("kind") not in kinds:
        raise ValueError(f"出口の部品の指定が正しくありません: {data!r}(選べるもの: {', '.join(kinds)})")
    return kinds[data["kind"]](**(data.get("params") or {}))


@dataclass(frozen=True)
class ExitRule:
    """出口ルール。利確と損切りの部品に、日数・条件による決済を足せる。

    max_days       買った足から何本たったら、その足の終値で売るか(0 なら使わない)
    exit_signals   成り立ったら、その足の終値で売る条件(どれか 1 つ)
    売りの判定の順: 利確・損切り → 条件による決済 → 日数
    """
    take_profit: TakeProfitPart
    stop_loss: StopLossPart
    max_days: int = 0
    exit_signals: tuple[ConditionRow, ...] = ()

    def __post_init__(self):
        if self.stop_loss.needs_take_profit and isinstance(self.take_profit, NoTakeProfit):
            raise ValueError("リスクリワードの損切りには、利確の値が要ります(利確を「なし」にできません)")
        if isinstance(self.max_days, bool) or not isinstance(self.max_days, int) or not 0 <= self.max_days <= 5000:
            raise ValueError(f"日数は 0 - 5000 の整数で指定してください: {self.max_days!r}")
        if (isinstance(self.take_profit, NoTakeProfit) and isinstance(self.stop_loss, NoStopLoss)
                and not self.max_days and not self.exit_signals):
            raise ValueError("売り方が 1 つもありません(利確・損切り・日数・条件のどれかを決めてください)")

    def describe(self) -> str:
        parts = []
        if not isinstance(self.take_profit, NoTakeProfit):
            parts.append(f"利確 {self.take_profit.name}")
        if not isinstance(self.stop_loss, NoStopLoss):
            parts.append(f"損切り {self.stop_loss.name}")
        if self.max_days:
            parts.append(f"{self.max_days} 本で決済")
        if self.exit_signals:
            parts.append("売り: " + " または ".join(row.label for row in self.exit_signals))
        return "、".join(parts)

    def to_json(self) -> dict:
        return {
            "take_profit": self.take_profit.to_json(),
            "stop_loss": self.stop_loss.to_json(),
            "max_days": self.max_days,
            "exit_signals": [row.to_json() for row in self.exit_signals],
        }

    @classmethod
    def from_json(cls, data) -> "ExitRule":
        if not isinstance(data, dict):
            raise ValueError("出口ルールの形が違います")
        return cls(
            _part_from_json(data.get("take_profit"), TAKE_PROFITS),
            _part_from_json(data.get("stop_loss"), STOP_LOSSES),
            data.get("max_days", 0),
            tuple(ConditionRow.from_json(row) for row in data.get("exit_signals") or []),
        )


# ---- 定義から動くストラテジー ----

@dataclass(frozen=True)
class StrategyDef:
    """ストラテジーの定義(名前・説明・買い条件・出口ルール)。スクリーニングとバックテストで共用する。"""
    name: str
    buy: ConditionSet
    exit: ExitRule
    description: str = ""  # 空なら、買い条件と出口ルールから作る

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("ストラテジーの名前がありません")
        if not self.buy.signals:
            raise ValueError(f"{self.name}: 買いのシグナルが 1 つもありません")

    def describe(self) -> str:
        return self.description or f"買い: {self.buy.describe()} / {self.exit.describe()}"

    def to_json(self) -> dict:
        return {"name": self.name, "description": self.description,
                "buy": self.buy.to_json(), "exit": self.exit.to_json()}

    @classmethod
    def from_json(cls, data) -> "StrategyDef":
        if not isinstance(data, dict):
            raise ValueError("ストラテジーの形が違います")
        return cls(str(data.get("name", "")), ConditionSet.from_json(data.get("buy")),
                   ExitRule.from_json(data.get("exit")), str(data.get("description", "")))


class RuleStrategy(Strategy):
    """StrategyDef(条件セット + 出口ルール)で動くストラテジー。

    買い: シグナルのどれかが成り立ち、同じ足でフィルターを満たした足の終値で買う。
          利確・損切りの値が決まらない合図は見送る
    売り: 出口ルール(ExitRule)のとおり
    """

    def __init__(self, definition: StrategyDef):
        self.definition = definition
        self.name = definition.name
        self.description = definition.describe()
        self._exit_cache: tuple[int, pd.Series] | None = None  # (足の id, 条件による決済が成り立つ足)

    def entries(self, bars: pd.DataFrame) -> list[Entry]:
        buy, rule = self.definition.buy, self.definition.exit
        result = buy.evaluate(bars)
        close = bars["Close"]
        entries = []
        for i in map(int, np.flatnonzero(result.buy.to_numpy())):
            price = float(close.iloc[i])
            if math.isnan(price):
                continue
            take_profit = rule.take_profit.level(bars, i, price)
            if take_profit is None:
                continue
            stop_loss = rule.stop_loss.level(bars, i, price, take_profit[0])
            if stop_loss is None:
                continue
            fired = [row.label for row, hit in zip(buy.signals, result.signal_hits) if hit.iloc[i]]
            note = "、".join(fired + [text for _, text in (take_profit, stop_loss) if text])
            entries.append(Entry(i, bars.index[i], price, take_profit[0], stop_loss[0], note))
        return entries

    def _exit_signal(self, bars: pd.DataFrame) -> pd.Series | None:
        """条件による決済が成り立つ足(どれか 1 つ)。同じ足では 1 回だけ計算する。"""
        rows = self.definition.exit.exit_signals
        if not rows:
            return None
        if self._exit_cache is None or self._exit_cache[0] != id(bars):
            hits = [row.built.compute(bars)[row.state].astype(bool) for row in rows]
            self._exit_cache = (id(bars), pd.concat(hits, axis=1).any(axis=1))
        return self._exit_cache[1]

    def exit_on(self, entry: Entry, bars: pd.DataFrame, i: int) -> Exit | None:
        exit_ = super().exit_on(entry, bars, i)  # 利確・損切り
        if exit_ is not None:
            return exit_
        close = float(bars["Close"].iloc[i])
        if math.isnan(close):
            return None
        signal = self._exit_signal(bars)
        if signal is not None and signal.iloc[i]:
            return Exit(i, bars.index[i], close, "条件で決済")
        rule = self.definition.exit
        if rule.max_days and i - entry.index >= rule.max_days:
            return Exit(i, bars.index[i], close, f"{rule.max_days} 本で決済")
        return None


# ---- 組み込みのストラテジー ----

def _row(condition: str, state: str, exclude: bool = False, **params) -> ConditionRow:
    return ConditionRow(condition, state, params, exclude)


# 組み込みのストラテジー(使い方の例を兼ねる)。旧 GC75-200 の出口(直近高値で利確・リスクリワード 2 倍で損切り・
# 利幅 3% 未満は見送り)は、出口の部品「直近高値」「リスクリワード」として残している
BUILTIN_STRATEGIES: tuple[StrategyDef, ...] = (
    StrategyDef(
        "SMA順張り",
        ConditionSet(
            "SMA順張り",
            signals=(_row("SMAクロス", "up", fast=5, slow=25),),
            filters=(_row("終値とSMA", "up", period=200), _row("終値とSMA", "down", exclude=True, period=75)),
        ),
        ExitRule(RecentHighTakeProfit(), RiskRewardStopLoss(ratio=2.0)),
        "SMA5 が SMA25 を上抜けで買い(終値が SMA200 より上、SMA75 より下なら見送り)。直近高値で利確、リスクリワード 2 倍で損切り",
    ),
    StrategyDef(
        "RSIダイバージェンス逆張り",
        ConditionSet("RSIダイバージェンス逆張り", signals=(_row("RSIダイバージェンス", "bullish"),)),
        ExitRule(FixedTakeProfit(pct=8.0), ATRStopLoss(multiple=2.0), max_days=40),
        "RSI の強気ダイバージェンスで買い。+8% で利確、ATR の 2 倍で損切り、40 本たったら決済",
    ),
)

# スクリーニングの組み込みの条件セット(ストラテジーの買い条件のほかに並べるもの)
BUILTIN_SETS: tuple[ConditionSet, ...] = (
    ConditionSet("SMAゴールデンクロス", signals=(
        _row("SMAクロス", "up", fast=5, slow=25), _row("SMAクロス", "up", fast=25, slow=75))),
    ConditionSet("パーフェクトオーダー(上昇)", filters=(_row("パーフェクトオーダー", "up"),)),
)


def all_strategies() -> dict[str, Strategy]:
    """組み込みのストラテジーと、usr_strategies.py のストラテジーを合わせて返す(名前 -> ストラテジー)。

    usr_strategies.py には、StrategyDef(定義)でも Strategy のクラス(コードで書いたもの)でも登録できる。
    usr_strategies.py はこのファイルを読み込むので、ここで後から読み込む(循環を避ける)。
    """
    from usr_strategies import USER_STRATEGIES

    strategies: dict[str, Strategy] = {d.name: RuleStrategy(d) for d in BUILTIN_STRATEGIES}
    for item in USER_STRATEGIES:
        strategy = RuleStrategy(item) if isinstance(item, StrategyDef) else item()
        if strategy.name in strategies:
            raise ValueError(f"ストラテジーの名前が重なっています: {strategy.name}")
        strategies[strategy.name] = strategy
    return strategies


# ---- ユーザープリセット(利用者が保存した条件セット) ----

def load_user_presets() -> tuple[list[ConditionSet], list[str]]:
    """screening_presets.json の条件セット。読めなかったものは飛ばし、その理由を 2 つ目に返す。"""
    if not PRESETS_PATH.exists():
        return [], []
    try:
        with open(PRESETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return [], [f"{PRESETS_PATH.name} を読めません: {e}"]
    presets, problems = [], []
    for item in data.get("presets", []) if isinstance(data, dict) else []:
        try:
            preset = ConditionSet.from_json(item)
            if not preset.name:
                raise ValueError("名前がありません")
            presets.append(preset)
        except ValueError as e:
            name = item.get("name", "?") if isinstance(item, dict) else "?"
            problems.append(f"「{name}」を読めません: {e}")
    return presets, problems


def save_user_presets(presets: list[ConditionSet]) -> None:
    """条件セットを screening_presets.json に保存する(.tmp に書いてから置き換える)。"""
    data = {"version": PRESETS_VERSION, "presets": [p.to_json() for p in presets]}
    temporary = PRESETS_PATH.with_name(PRESETS_PATH.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temporary, PRESETS_PATH)


def main() -> int:
    strategies = all_strategies()
    if len(sys.argv) < 2:
        for name, strategy in strategies.items():
            print(f"{name:16} {strategy.description}")
        return 0
    code = sys.argv[1].upper().removesuffix(".T")
    names = sys.argv[2:] or list(strategies)
    unknown = [n for n in names if n not in strategies]
    if unknown:
        print(f"ストラテジーがありません: {', '.join(unknown)}(一覧: python core_strategies.py)")
        return 1
    bars = load_saved(csv_path_for(code))
    if bars is None:
        print(f"{code} のデータがありません。先に python core_gather.py {code} を実行してください。")
        return 1
    bars = bars.dropna(subset=["Close"])
    for name in names:
        trades = strategies[name].trades(bars)
        print(f"== {name} / {code}: {len(trades)} 取引")
        for t in trades:
            e = t.entry
            line = f"  買 {e.date:%Y-%m-%d} {e.price:>10,.1f}  利確 {e.take_profit:>10,.1f}  損切り {e.stop_loss:>10,.1f}"
            if t.exit is None:
                line += "  → 保有中"
            else:
                line += f"  → {t.exit.reason} {t.exit.date:%Y-%m-%d} {t.exit.price:>10,.1f} ({t.return_pct:+.2f}%)"
            print(line + (f"  [{e.note}]" if e.note else ""))
        closed = [t for t in trades if t.exit is not None]
        if closed:
            wins = sum(t.return_pct > 0 for t in closed)
            average = sum(t.return_pct for t in closed) / len(closed)
            print(f"  決済 {len(closed)} 回: 勝ち {wins} 回(勝率 {wins / len(closed):.0%})、平均損益 {average:+.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
