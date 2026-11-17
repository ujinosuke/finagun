"""ストラテジー(売買戦略)の基本クラスとその種類。

ストラテジーは「いつ・いくらで買い、どこで利確・損切りするか」を決める。
複数銘柄の集計・手数料・資金管理などは、バックテスト(fina_bktest.py)が受け持つ。

ストラテジーの役目は 2 つ:
    entries()  足を最初から最後まで調べ、買いの合図(Entry)の一覧を返す。
               Entry には、買う日・買値・利確の値(take_profit)・損切りの値(stop_loss)を入れる
    exit_on()  保有中の足 1 本ごとに呼ばれ、売るなら Exit を返す。
               初期の実装は「終値が take_profit 以上なら利確、stop_loss 以下なら損切り」

trades() は、1 銘柄について「買い → 売り」を 1 つずつ順に進め、取引(Trade)の一覧を返す。
保有中に出た買いの合図は使わない(同時に持つのは 1 つだけ)。
fina_bktest.py は、各銘柄の trades() を集めて成績を出す。

約束ごと(CLAUDE.md も参照):
    - 買いのみ(レバレッジなし)。売りから入る取引は扱わない
    - 断りがなければ、株価は終値(Close)だけを使う。売買も、判定した足の終値で行ったことにする
    - その足の時点で分かることだけを使う(未来の足を見ない)。
      山や谷のように、後の足が出そろって確定するものは、確定した足から使う

新しいストラテジーの作り方:
    1. Strategy を継承し、name と description を決める
    2. entries() を書く。利確・損切りが「値に届いたら売る」でなければ exit_on() も書く
    3. 下の STRATEGIES に登録する
    利用者が自分で作るストラテジーは、このファイルではなく usr_strategies.py に書く。

使い方(動作の確認):
    python fina_strategies.py                 # 登録されているストラテジーの一覧
    python fina_strategies.py 7203            # 7203 で全ストラテジーの取引を表示
    python fina_strategies.py 7203 GC75-200   # ストラテジーを指定
"""

import math
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from fina_condition import pivots
from fina_gather import csv_path_for, load_saved
from fina_indicators import sma


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

    def trades(self, bars: pd.DataFrame, start: pd.Timestamp | None = None) -> list[Trade]:
        """1 銘柄の取引の一覧。同時に持つのは 1 つだけで、保有中に出た買いの合図は使わない。

        start を渡すと、その日より前の買いの合図は使わない(バックテストの期間の始まり)。
        インジケーターは start より前の足も使って計算するので、期間の初日から合図を出せる。
        """
        result = []
        free_from = 0  # この行番号以降の合図なら買える
        for entry in self.entries(bars):
            if entry.index < free_from or (start is not None and entry.date < start):
                continue
            exit_ = None
            for i in range(entry.index + 1, len(bars)):
                exit_ = self.exit_on(entry, bars, i)
                if exit_ is not None:
                    break
            result.append(Trade(entry, exit_))
            if exit_ is None:  # データの最後まで保有中
                break
            free_from = exit_.index + 1  # 売った足の次の足から、また買える
        return result


def recent_high_above(close: pd.Series, i: int, price: float, left: int, right: int, lookback: int):
    """i 本目の時点で確定している山(終値)のうち、price より高く、i に一番近いものを返す。

    山は左 left 本・右 right 本より高い足(fina_condition.pivots と同じ)。
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


class GoldenCross75200(Strategy):
    """75 日線と 200 日線のゴールデンクロスで買う。直近高値で利確、リスクリワード 2 倍の位置で損切り。

    買い  : SMA75 が SMA200 を下から上に抜けた日(前日 SMA75 ≦ SMA200、当日 SMA75 > SMA200)の終値で買う
    利確  : 直近高値。その日までに確定した終値の山のうち、買値より高く、一番最近のもの。
            山は左右 PIVOT_LEFT / PIVOT_RIGHT 本より終値が高い足で、LOOKBACK 本前までさかのぼって探す。
            見つからなければ、その合図は見送る
    見送り: 利幅(直近高値 - 買値)が買値の MIN_REWARD(3%)未満なら、その合図は見送る
    損切り: リスクリワードが 2 倍になる位置。利幅(直近高値 - 買値)の半分だけ買値から下
            (損切り = 買値 - (直近高値 - 買値) / 2)
    売り  : 終値が利確の値以上、または損切りの値以下になった日の終値で売る
    """

    name = "GC75-200"
    description = "SMA75/200 のゴールデンクロスで買い、直近高値で利確、リスクリワード 2 倍で損切り"
    FAST = 75
    SLOW = 200
    PIVOT_LEFT = 5     # 山の判定に使う左側の本数
    PIVOT_RIGHT = 5    # 山の判定に使う右側の本数(この本数だけ確定が遅れる)
    LOOKBACK = 250     # 直近高値をさかのぼって探す本数(日足でおよそ 1 年)
    REWARD_RISK = 2.0  # 利幅 / 損失幅
    MIN_REWARD = 0.03  # 利幅の下限(買値に対する割合)。これ未満の合図は見送る

    def entries(self, bars: pd.DataFrame) -> list[Entry]:
        close = bars["Close"]
        fast, slow = sma(close, self.FAST), sma(close, self.SLOW)
        crossed = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        result = []
        for i in [int(n) for n in crossed.to_numpy().nonzero()[0]]:
            price = close.iloc[i]
            high = recent_high_above(close, i, price, self.PIVOT_LEFT, self.PIVOT_RIGHT, self.LOOKBACK)
            if high is None:
                continue  # 直近高値がないので見送る
            high_index, take_profit = high
            if take_profit - price < price * self.MIN_REWARD:
                continue  # 利幅が小さすぎるので見送る
            stop_loss = price - (take_profit - price) / self.REWARD_RISK
            result.append(Entry(
                i, bars.index[i], price, take_profit, stop_loss,
                note=f"直近高値 {bars.index[high_index]:%Y-%m-%d} {take_profit:,.1f}",
            ))
        return result


# 組み込みのストラテジー(名前 -> クラス)
STRATEGIES: dict[str, type[Strategy]] = {
    GoldenCross75200.name: GoldenCross75200,
}


def all_strategies() -> dict[str, type[Strategy]]:
    """組み込みのストラテジーと、usr_strategies.py のストラテジーを合わせて返す。

    usr_strategies.py はこのファイルを読み込むので、ここで後から読み込む(循環を避ける)。
    """
    from usr_strategies import USER_STRATEGIES

    duplicated = STRATEGIES.keys() & USER_STRATEGIES.keys()
    if duplicated:
        raise ValueError(f"ストラテジーの名前が組み込みと重なっています: {', '.join(sorted(duplicated))}")
    return STRATEGIES | USER_STRATEGIES


def main() -> int:
    strategies = all_strategies()
    if len(sys.argv) < 2:
        for name, cls in strategies.items():
            print(f"{name:12} {cls.description}")
        return 0
    code = sys.argv[1].upper().removesuffix(".T")
    names = sys.argv[2:] or list(strategies)
    unknown = [n for n in names if n not in strategies]
    if unknown:
        print(f"ストラテジーがありません: {', '.join(unknown)}(一覧: python fina_strategies.py)")
        return 1
    bars = load_saved(csv_path_for(code))
    if bars is None:
        print(f"{code} のデータがありません。先に python fina_gather.py {code} を実行してください。")
        return 1
    bars = bars.dropna(subset=["Close"])
    for name in names:
        trades = strategies[name]().trades(bars)
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
