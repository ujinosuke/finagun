"""ユーザー定義のストラテジー(売買戦略)。

このファイルは、利用者が自分のストラテジーを書き足すためのものです。
プログラムを書かなくても、AI(Claude Code など)に下の「AI への指令の仕方」のとおりに頼めば作れます。
ここに登録したストラテジーは、core_strategies.py の組み込みのストラテジーと同じように、
fina_bktest.py(成績の検証)と fina_screening.py(プリセット。買い条件が展開される)で使えます。

ストラテジーは「買い条件(条件セット)+ 出口ルール」で定義します(設計は pico_strategy-screening-design.md)。
    買い条件  シグナル(どれか 1 つで買いの合図)と、フィルター(すべて満たすこと。「除外」は成り立ったら見送る)
    出口ルール 利確・損切りの決め方の部品と、日数・条件による決済
条件は core_condition.py のコンディションを、種類の名前・状態・パラメータで指定します。

======================================================================
AI への指令の仕方
======================================================================

AI には、次のひな形を埋めて渡してください。決めていない項目は「おまかせ」と書けば、
AI が案を出し、どう決めたかを説明します。

----------------------------------------------------------------------
usr_strategies.py にストラテジーを 1 つ追加してください。
このファイルの先頭の説明(設計の手順と約束ごと)に従ってください。

名前        : (例: GC25-75押し目)  ← 組み込みの名前と重ねない
説明        : (例: SMA25 が SMA75 を上抜けたら買う順張り)
買いのシグナル: (例: SMAクロス(25,75) のゴールデン)  ← 複数書けば「どれか 1 つ」
フィルター  : (例: パーフェクトオーダーが上昇)       ← すべて満たすこと。なければ「なし」
除外        : (例: 終値が SMA200 より下なら買わない) ← なければ「なし」
利確        : (例: 買値の +8%)
損切り      : (例: 買値の -4%)
その他の売り: (例: 20 日たっても決済していなければ、その日の終値で売る) ← なければ「なし」

作ったら python core_strategies.py 7203 <名前> で動かし、取引の一覧を見せてください。
----------------------------------------------------------------------

頼み方のこつ:
    - 条件は、今あるコンディション(下の「使える部品」)の組み合わせで書けるか、まず考える。
      ないときは「○○のコンディションを作ってから」と頼む(core_condition.py に足すことになる)
    - 「直近高値」「サポート」のように人によって意味が違う言葉は、決め方も書く
    - できあがったら、取引の一覧で、合図の日がチャート(python gui_chart.py 7203)と
      合っているかを確かめる。違っていたら、どの日がどう違うかを AI に伝えて直してもらう

======================================================================
設計の手順(AI と、プログラムを書く人向け)
======================================================================

1. core_strategies.StrategyDef で定義を作る
       StrategyDef(名前, 買い条件, 出口ルール, 説明)
       買い条件  ConditionSet(名前, signals=(行, ...), filters=(行, ...))
       行        ConditionRow(種類の名前, 状態, {パラメータ}, exclude)
                 exclude=True はフィルター層で「除外」(成り立ったら見送る)
       出口ルール ExitRule(利確の部品, 損切りの部品, max_days=日数, exit_signals=(行, ...))
2. 下の USER_STRATEGIES に足す
3. python core_strategies.py <銘柄コード> <名前> で取引の一覧を表示して確かめる

使える部品:
    コンディション(種類の名前: 状態 / パラメータ)   一覧は core_condition.CONDITION_TYPES
        RSIダイバージェンス  bullish(強気)・bearish(弱気) / なし
        SMAクロス            up(ゴールデン)・down(デッド) / fast, slow
        パーフェクトオーダー  up(上昇)・down(下降) / p1, p2, p3, p4
        終値とSMA            up(終値が上)・down(終値が下) / period
    利確(core_strategies.TAKE_PROFITS)
        FixedTakeProfit(pct=10.0)            買値の +pct%
        RecentHighTakeProfit(left=5, right=5, lookback=250, min_reward=3.0)
                                             直近高値(利幅が min_reward% 未満なら見送り)
        NoTakeProfit()                       利確しない
    損切り(core_strategies.STOP_LOSSES)
        FixedStopLoss(pct=5.0)               買値の -pct%
        RiskRewardStopLoss(ratio=2.0)        利幅 ÷ ratio だけ買値から下
        ATRStopLoss(multiple=2.0, period=14) 買値 - multiple × ATR
        NoStopLoss()                         損切りしない

    定義で書けないルール(例: 独自の計算で買う)は、core_strategies.Strategy を継承したクラスでも書ける。
    entries(bars) で core_strategies.Entry の一覧を返し、必要なら exit_on() を上書きする。
    クラスも USER_STRATEGIES に足せる(クラスのまま足す。name と description をクラスに書く)。

約束ごと(必ず守る):
    - 買いのみ(レバレッジなし)。売りから入る取引は作らない
    - 断りがなければ、株価は終値(Close)だけを使う(CLAUDE.md のルール)
    - 未来の足を見ない。i 本目の合図や売りは、0〜i 本目の足だけから決める
    - 変えるのはこのファイルだけ。core_strategies.py などの共通のファイルを変える必要があるときは、
      変えずに、何をどう変えたいかを利用者に伝える
    - 説明やコメントは日本語で書く

書き方の例(このとおりに足せば動きます):

    USER_STRATEGIES = [
        StrategyDef(
            "GC25-75押し目",
            ConditionSet(
                "GC25-75押し目",
                signals=(ConditionRow("SMAクロス", "up", {"fast": 25, "slow": 75}),),
                filters=(ConditionRow("終値とSMA", "down", {"period": 200}, exclude=True),),
            ),
            ExitRule(FixedTakeProfit(pct=8.0), FixedStopLoss(pct=4.0), max_days=20),
            "SMA25 が SMA75 を上抜けたら買い(終値が SMA200 より下なら見送り)。+8% で利確、-4% で損切り、20 本で決済",
        ),
    ]
"""

from core_strategies import (  # noqa: F401  ストラテジーを書くときに使う部品
    ATRStopLoss, ConditionRow, ConditionSet, Entry, Exit, ExitRule, FixedStopLoss, FixedTakeProfit, NoStopLoss,
    NoTakeProfit, RecentHighTakeProfit, RiskRewardStopLoss, Strategy, StrategyDef,
)


# ユーザー定義のストラテジー。StrategyDef(定義)か、Strategy を継承したクラスを並べる
USER_STRATEGIES: list = [
]
