# ToreCan 開発のルール

## 実装の基本方針

- コードは Python で書く。
- GUI は tkinter で作る。
- コメントは日本語で書く。

## ファイル名の接頭語

新しいファイルを作るときは、役割に合わせて次の接頭語を付ける。

| 接頭語 | 役割 | 例 |
|---|---|---|
| `fina_` | 主要プログラム(利用者が起動する画面のあるプログラム) | `fina_bktest.py`、`fina_techtrainer.py` |
| `gui_` | 裏方のフォーム系。複数のプログラムで使う tkinter の画面部品 | `gui_chart.py`、`gui_widgets.py` |
| `core_` | 裏方の計算系。画面を持たない計算・データの処理 | `core_strategies.py`、`core_gather.py` |
| `usr_` | ユーザー編集可能なプログラム | `usr_strategies.py` |

- `core_` のファイルでは tkinter を使わない(画面の部品は `gui_` に置く)。
- ファイルを追加・改名したら、README.md のプログラム一覧も直す。

## 株価の扱い

- 日足（もしくはそれ以上の足）でのトレードに対応している。
- インジケーター・オシレーター・コンディション・スクリーニングなどで株価を使うときは、断りがなければ **終値(`Close`)だけ** を対象にする。
  - 始値・高値・安値を使うのは、仕様で明示されたときだけにする。
- 例外: `core_condition.py` の RSI ダイバージェンス(`RSIDivergence`)は、TradingView の「RSI Divergence Indicator」に合わせて、株価の山では高値、谷では安値を比べる(RSI 自体は終値で計算する)。
