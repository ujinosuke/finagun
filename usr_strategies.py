"""ユーザー定義のストラテジー(売買戦略)。

このファイルは、利用者が自分のストラテジーを書き足すためのものです。
プログラムを書かなくても、AI(Claude Code など)に下の「AI への指令の仕方」のとおりに頼めば作れます。
ここに登録したストラテジーは、core_strategies.py の組み込みのストラテジーと同じように
fina_bktest.py で使えます(core_strategies.all_strategies() が両方を集めます)。

======================================================================
AI への指令の仕方
======================================================================

AI には、次のひな形を埋めて渡してください。決めていない項目は「おまかせ」と書けば、
AI が案を出し、どう決めたかを説明します。

----------------------------------------------------------------------
usr_strategies.py にストラテジーを 1 つ追加してください。
このファイルの先頭の説明(設計の手順と約束ごと)に従ってください。

名前      : (例: RSI30-Rebound)  ← 英数字と記号で短く。組み込みの名前と重ねない
説明      : (例: RSI が 30 を下から上に抜けたら買う逆張り)
買いの条件: (例: RSI(14) が前日 30 以下、当日 30 より上)
見送る条件: (例: 終値が SMA200 より下なら買わない)  ← なければ「なし」
利確      : (例: 買値の +8%)
損切り    : (例: 買値の -4%)
その他の売り: (例: 20 日たっても決済していなければ、その日の終値で売る) ← なければ「なし」
使う株価  : 終値  ← 高値・安値・始値を使うときだけ書き換える
足        : 日足

作ったら python core_strategies.py 7203 <名前> で動かし、取引の一覧を見せてください。
----------------------------------------------------------------------

頼み方のこつ:
    - 条件は「いつの足の、どの値が、何と比べてどうなったら」まで書く。
      「上がったら」ではなく「終値が前日の終値より 3% 以上高ければ」のように書く
    - 「クロス」は「前日は下(以下)、当日は上」のように、前日と当日の関係で書くと誤解がない
    - 「直近高値」「サポート」のように人によって意味が違う言葉は、決め方も書く
      (例: 直近高値 = 左右 5 本より終値が高い足のうち、一番最近のもの)
    - 期間や % などの数値は、後から変えたいものとして伝える(AI がクラスの定数にします)
    - できあがったら、取引の一覧で、合図の日がチャート(python gui_chart.py 7203)と
      合っているかを確かめる。違っていたら、どの日がどう違うかを AI に伝えて直してもらう

======================================================================
設計の手順(AI と、プログラムを書く人向け)
======================================================================

1. core_strategies.Strategy を継承したクラスを作る
       name        短い名前(英数字と記号)。core_strategies.STRATEGIES の名前と重ねない
       description 売買のルールを 1 行で
       docstring   買い・見送り・利確・損切り・その他の売りのルールを、数値も含めて日本語で書く
2. entries(bars) を書き、買いの合図(core_strategies.Entry)を日付の古い順のリストで返す
       Entry(index, date, price, take_profit, stop_loss, note)
       index / date      買う足の行番号と日付
       price             買値。断りがなければ、その足の終値
       take_profit       利確の値。利確しないなら math.inf
       stop_loss         損切りの値。損切りしないなら 0
       note              買った理由など(表示用。なくてもよい)
3. 売りが「終値が take_profit 以上で利確、stop_loss 以下で損切り」だけなら、exit_on() は書かない。
   日数で売る・指標で売るなど、ほかの売り方があるときは exit_on(entry, bars, i) を上書きし、
   売るなら core_strategies.Exit(index, date, price, reason) を、売らないなら None を返す。
   利確・損切りも続けて使うときは、最後に super().exit_on(entry, bars, i) を返す
4. 下の USER_STRATEGIES に登録する
5. python core_strategies.py <銘柄コード> <名前> で取引の一覧を表示して確かめる

使える部品(新しく作る前に、まずこれらを使う):
    core_indicators.sma(series, period)              単純移動平均
    core_oscillators.RSI().compute(bars)["RSI"]      RSI(14)
    core_condition.pivots(series, left, right, find_high)
                                                     山・谷の行番号の一覧(右 right 本後に確定)
    core_strategies.recent_high_above(close, i, price, left, right, lookback)
                                                     i 本目の時点で確定している、price より高い直近の山
    core_condition.CONDITIONS                        コンディション(RSI ダイバージェンスなど)

約束ごと(必ず守る):
    - 買いのみ(レバレッジなし)。売りから入る取引は作らない
    - 断りがなければ、株価は終値(Close)だけを使う(CLAUDE.md のルール)
    - 未来の足を見ない。i 本目の合図や売りは、0〜i 本目の足だけから決める。
      shift(-1) や、i より後の行を読むこと(iloc[i + 1] など)はしない。
      山・谷は、右側の本数が出そろった足から使う
    - 計算に必要な本数がそろわない最初の足(SMA200 なら最初の 199 本)では合図を出さない
    - 期間・閾値・% などの数値は、クラスの定数(大文字の名前)にして、意味をコメントに書く
    - 変えるのはこのファイルだけ。core_strategies.py などの共通のファイルを変える必要があるときは、
      変えずに、何をどう変えたいかを利用者に伝える
    - 説明やコメントは日本語で書く

書き方の例(このとおりに登録すれば動きます):

    class Breakout20(Strategy):
        \"\"\"終値が過去 20 日の最高値を上抜けたら買う。+10% で利確、-5% で損切り。\"\"\"

        name = "Breakout20"
        description = "終値が過去 20 日の最高値を上抜けたら買い、+10% で利確、-5% で損切り"
        PERIOD = 20         # 最高値を見る日数(当日を含まない)
        TAKE_PROFIT = 0.10  # 利確: 買値の +10%
        STOP_LOSS = 0.05    # 損切り: 買値の -5%

        def entries(self, bars):
            close = bars["Close"]
            highest = close.shift(1).rolling(self.PERIOD, min_periods=self.PERIOD).max()
            signal = close > highest
            return [
                Entry(i, bars.index[i], close.iloc[i],
                      close.iloc[i] * (1 + self.TAKE_PROFIT), close.iloc[i] * (1 - self.STOP_LOSS))
                for i in map(int, signal.to_numpy().nonzero()[0])
            ]

    USER_STRATEGIES = {
        Breakout20.name: Breakout20,
    }
"""

import math  # noqa: F401  ストラテジーで math.inf などを使うときのため

import pandas as pd  # noqa: F401

from core_strategies import Entry, Exit, Strategy  # noqa: F401


# ここにストラテジーのクラスを書く


# ユーザー定義のストラテジー(名前 -> クラス)。作ったクラスをここに登録する
USER_STRATEGIES: dict[str, type[Strategy]] = {
}
