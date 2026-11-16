# finagun(ファイナガン)

finagun は、金融・投資系のプログラム集です。

- すべてのプログラムは Python(3.12 以上)で記述されています。
- GUI エンジンは tkinter です。

## トレード方針

- 日足でのスイングトレードが主体です。
- 基本は **レバレッジなしの買いのみ** のトレードです。
  - FX など、レバレッジや売りを簡単に扱える商品はこの限りではありません。
- 現在のバージョンが扱うのは **東証プライム市場の銘柄のみ** です。株式のため、レバレッジなしの買いのみが前提になります。

## プログラム一覧

### 主要プログラム

| ファイル | 内容 |
|---|---|
| `finagun.py` | 資産管理プログラム。市場終了時に毎日更新し、資産の推移やポートフォリオなどを表示する |
| `fina_bktest.py` | バックテストを行うプログラム |
| `fina_techtrainer.py` | テクニカルトレードの訓練用プログラム |
| `fina_screening.py` | スクリーニングプログラム |
| `fina_watchlist.py` | 株価チャートのウォッチリスト。銘柄コードや銘柄名で検索して、グループごとに複数の銘柄を登録できる。登録内容とチャートの描画の状態は `watchlist.json` に保存し、次回の起動時に自動で読み込む |

### 裏方のプログラム

| ファイル | 内容 |
|---|---|
| `fina_gather.py` | yfinance を使って、銘柄一覧に載っている全銘柄(PRO Market を除く)の日足データを取得する。CSV 形式で保存される。2 回目以降は更新分だけを取得する |
| `fina_listup_stocks.py` | 東証プライムなどの銘柄一覧データ(`data.xls`)から CSV を生成する |
| `fina_chart.py` | チャート表示の基本プログラム。matplotlib を使用する |
| `fina_strategies.py` | ストラテジーの基本クラスとその種類 |
| `fina_indicators.py` | インジケーターの基本クラスとその種類 |
| `fina_oscillators.py` | オシレーターの基本クラスとその種類 |
| `fina_condition.py` | 状態の基本クラスとその種類 |
| `fina_anomalies.py` | アノーマリーの基本クラスとその種類 |

## データフォルダ(git 管理外)

| フォルダ | 内容 |
|---|---|
| `listed_stocks/` | 銘柄一覧。毎月更新する。例: `T2610.xls`(東証 2026 年 10 月の銘柄)。この xls から `fina_listup_stocks.py` で生成した CSV も置く |
| `stock_database/` | 各銘柄ごとの CSV ファイル(例: `T7203.csv`)。2016 年 1 月以降の日足データ(OHLCV) |

## 必要環境

- Python 3.12 以上
- tkinter(Python 標準。Windows の公式インストーラーなら同梱)
- 主な外部ライブラリ: yfinance、matplotlib、pandas、openpyxl(Excel の読み込み用。旧形式の `.xls` を読む場合は xlrd も必要)

## セットアップ

```bash
git clone https://github.com/ujinosuke/finagun.git
cd finagun
pip install -r requirements.txt
```

## 使い方

```bash
python finagun.py            # 資産管理
python fina_bktest.py        # バックテスト
python fina_techtrainer.py   # テクニカルトレードの訓練
python fina_screening.py     # スクリーニング(先に fina_gather.py で株価を最新にしておく)
python fina_watchlist.py     # ウォッチリスト(登録内容はルートの watchlist.json に保存される)
python fina_listup_stocks.py # 銘柄一覧の CSV 生成
python fina_gather.py        # 銘柄情報の取得
python fina_chart.py 7203    # チャートの表示(銘柄コードを指定)
```

## 免責事項

本プロジェクトは情報提供・学習を目的としており、投資助言ではありません。投資判断は自己責任で行ってください。

## ライセンス

[MIT License](LICENSE)
