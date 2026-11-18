# ToreCan(トレキャン)

ToreCan は、金融・投資系のプログラム集です。

「トレキャン」の「キャン」には、3 つの意味を込めています。「じぶんでできる・えがける・学べる」

- **Can** … じぶんでできる
- **Canvas** … じぶんでえがける
- **Campus** … 大学構内のような(綴りは Cam ですが、日本語では「キャン」と読むので)

- すべてのプログラムは Python(3.12 以上)で記述されています。
- GUI エンジンは tkinter です。

## トレード方針

- 日足でのスイングトレードが主体です。
- 基本は **レバレッジなしの買いのみ** のトレードです。
  - FX など、レバレッジや売りを簡単に扱える商品はこの限りではありません。
- 現在のバージョンが扱うのは **東証の銘柄のみ** です。株式のため、レバレッジなしの買いのみが前提になります。米国株や指標は扱っていません。

## プログラム一覧

### 主要プログラム

| ファイル | 内容 |
|---|---|
| `ToreCan.py` | 統合プログラム。ホーム画面 |
| `fina_asset.py` | 資産管理プログラム。取引の手入力・証券会社の CSV の取り込みから、保有(銘柄 × 証券会社 × 口座区分)・評価額・含み損益・実現損益を集計する。資産推移と配分のグラフ、配当の参考表示、ヒートマップもある。記録は `asset.json` に保存する。仕様は `fina_asset_spec.md`、設計は `fina_asset_design.md`・`fina_asset_design_phase2.md` |
| `fina_bktest.py` | バックテストを行うプログラム |
| `fina_techtrainer.py` | テクニカルトレードの訓練用プログラム |
| `fina_screening.py` | スクリーニングプログラム。結果の銘柄をウォッチリストに追加できる |
| `fina_watchlist.py` | 株価チャートのウォッチリスト。銘柄コードや銘柄名で検索して、グループごとに複数の銘柄を登録できる。登録内容とチャートの描画の状態は `watchlist.json` に保存し、次回の起動時に自動で読み込む |
| `fina_compare.py` | 銘柄を比較する。比較表(株価・時価総額・EV/Rev・EV/EBITDA・PER・PSR・配当利回り と、騰落率・52週高値安値比・移動平均乖離・RSI・ボラティリティ・ATR・β・売買代金)、株価推移の相関マトリクス、業績(売上高・営業利益・純利益)の推移の 3 つのビュー。Excel に全部を出力できる。設計は `fina_compare_design.md` |

### 裏方のプログラム

接頭語で 2 つに分けています。

#### フォーム系(`gui_`)… 複数のプログラムで使う tkinter の画面部品

| ファイル | 内容 |
|---|---|
| `gui_chart.py` | チャート表示の基本プログラム。matplotlib を使用する |
| `gui_widgets.py` | 複数のプログラムで使い回す tkinter の部品(銘柄の検索欄 `StockSearchBox`、ウォッチリストへの追加 `add_to_watchlist` など) |
| `gui_treemap.py` | ツリーマップ(ヒートマップ)の描画部品。四角の大きさで量を、色で上がり下がりを示す |
| `gui_corrmatrix.py` | 相関マトリクスの描画部品。+1 に近いほど青、-1 に近いほど赤で、計算に使った日数も出す |

#### 計算系(`core_`)… 画面を持たない、計算・データの処理

| ファイル | 内容 |
|---|---|
| `core_gather.py` | yfinance を使って、銘柄一覧に載っている全銘柄(PRO Market を除く)の日足データを取得する。CSV 形式で保存される。2 回目以降は更新分だけを取得する |
| `core_listup_stocks.py` | 東証プライムなどの銘柄一覧データ(`data.xls`)から CSV を生成する |
| `core_asset.py` | 資産管理のデータ(商品・取引・保有)と保存、取引からの保有集計(移動平均法)・実現損益 |
| `core_asset_value.py` | 資産管理の評価(資産クラスごとの評価額・含み損益)、資産推移のスナップショットと過去の推移の計算、配分・期間騰落率 |
| `core_asset_import.py` | 証券会社の CSV から取引を取り込む。会社・形式ごとのアダプタ(楽天証券の保有商品一覧など) |
| `core_dividend.py` | yfinance から保有銘柄の配当情報を取得し、`dividend_cache.json` にキャッシュする |
| `core_compare.py` | 銘柄比較の計算。テクニカル指標・相関・ベータ(保存済みの日足から)と、業績の取得(yfinance。`financials_cache.json` にキャッシュ) |
| `core_watchlist.py` | ウォッチリストの保存ファイル(`watchlist.json`)の読み書き。他のプログラムから銘柄を足すのにも使う |
| `core_stocks.py` | 銘柄一覧の読み込みと、銘柄コード・銘柄名での銘柄の検索(GUI を含まない部分) |
| `core_strategies.py` | ストラテジーの基本クラスとその種類 |
| `core_indicators.py` | インジケーターの基本クラスとその種類 |
| `core_oscillators.py` | オシレーターの基本クラスとその種類 |
| `core_condition.py` | 状態の基本クラスとその種類 |
| `core_anomalies.py` | アノーマリーの基本クラスとその種類 |

## ユーザー編集可能なプログラム

| ファイル | 内容 |
|---|---|
| `usr_strategies.py` | ユーザー定義のストラテジー |

## データフォルダ(git 管理外)

| フォルダ | 内容 |
|---|---|
| `listed_stocks/` | 銘柄一覧。毎月更新する。例: `T2610.xls`(東証 2026 年 10 月の銘柄)。この xls から `core_listup_stocks.py` で生成した CSV も置く |
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
python ToreCan.py            # ホーム画面
python fina_asset.py         # 資産管理
python fina_bktest.py        # バックテスト
python fina_techtrainer.py   # テクニカルトレードの訓練
python fina_screening.py     # スクリーニング(先に core_gather.py で株価を最新にしておく)
python fina_watchlist.py     # ウォッチリスト(登録内容はルートの watchlist.json に保存される)
python fina_compare.py       # 銘柄の比較表(登録内容はルートの compare.json に保存される)
python core_listup_stocks.py # 銘柄一覧の CSV 生成
python core_gather.py        # 銘柄情報の取得
python gui_chart.py 7203    # チャートの表示(銘柄コードを指定)
python core_strategies.py 7203  # ストラテジーの取引の一覧(動作の確認用。ストラテジー名も指定できる)
```

## 免責事項

本プロジェクトは情報提供・学習を目的としており、投資助言ではありません。投資判断は自己責任で行ってください。

## ライセンス

[MIT License](LICENSE)
