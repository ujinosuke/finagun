# ToreCan 開発のルール

## 実装の基本方針

- コードは Python で書く。
- GUI は tkinter で作る。
- コメントは日本語で書く。

## バージョン

- バージョンは `core_settings.py` の `__version__` の 1 か所だけで管理する(ホーム画面と各プログラムのタイトルバーに出る)。
- 機能を足したらマイナー(0.**1**.0)、直しだけならパッチ(0.1.**1**)を上げ、README.md の「更新履歴」に 1 行足す。
- 新しい主要プログラムのタイトルバーは `app_title("名前")` で作る。

## 見た目

- 主要プログラムの `main()` では、`tk.Tk()` を作った直後に `gui_theme.apply_theme(root)` を呼ぶ。
- 色・フォント・余白は `gui_theme.py` の定数(`ACCENT`・`CARD`・`PAD` など)とスタイル名(`Card.TFrame`・`Accent.TButton` など)を使い、部品ごとに直接書かない。
- アイコンは `gui_icons.icon("名前", サイズ, 色)` で使う(Material Icons の Filled。足すときは `gui_icons.ICONS` に足して PNG を作る)。

## 全体設定

- 全プログラム共通の設定は `settings.json`(`core_settings.py` で読み書き)に置く。プログラムごとの保存ファイルに同じ設定を重ねて持たない。
- 上昇・下落(利益・損失)の色は `core_settings.market_colors()`(チャート)・`profit_colors()`(文字)を使い、色を直接書かない。

## ファイル名の接頭語

新しいファイルを作るときは、役割に合わせて次の接頭語を付ける。

| 接頭語 | 役割 | 例 |
|---|---|---|
| `fina_` | 主要プログラム(利用者が起動する画面のあるプログラム) | `fina_bktest.py`、`fina_techtrainer.py` |
| `gui_` | 裏方のフォーム系。複数のプログラムで使う tkinter の画面部品 | `gui_chart.py`、`gui_widgets.py` |
| `core_` | 裏方の計算系。画面を持たない計算・データの処理 | `core_strategies.py`、`core_gather.py` |
| `usr_` | ユーザー編集可能なプログラム | `usr_strategies.py` |

- 例外: 全体の入り口のホーム画面は `ToreCan.py`(接頭語なし)。
- `core_` のファイルでは tkinter を使わない(画面の部品は `gui_` に置く)。
- ファイルを追加・改名したら、README.md のプログラム一覧も直す。

## 株価の扱い

- 日足（もしくはそれ以上の足）でのトレードに対応している。
- インジケーター・オシレーター・コンディション・スクリーニングなどで株価を使うときは、断りがなければ **終値(`Close`)だけ** を対象にする。
  - 始値・高値・安値を使うのは、仕様で明示されたときだけにする。
- 例外: `core_condition.py` の RSI ダイバージェンス(`RSIDivergence`)は、TradingView の「RSI Divergence Indicator」に合わせて、株価の山では高値、谷では安値を比べる(RSI 自体は終値で計算する)。
- 例外: `core_compare.py` の 52 週高値・安値は、一般的な定義に合わせて日中の高値(`High`)・安値(`Low`)を使う(2026-11-18 本人決定)。
- 例外: `core_compare.py` の ATR は、定義どおり高値・安値・終値を使う(2026-11-18 本人決定)。
