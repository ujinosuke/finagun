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

### 裏方のプログラム

| ファイル | 内容 |
|---|---|
| `fina_gather.py` | yfinance を使って各銘柄の情報を取得する。CSV 形式で保存される |
| `fina_chart.py` | チャート表示の基本プログラム。matplotlib を使用する |

## 必要環境

- Python 3.12 以上
- tkinter(Python 標準。Windows の公式インストーラーなら同梱)
- 主な外部ライブラリ: yfinance、matplotlib

## セットアップ

```bash
git clone https://github.com/ujinosuke/finagun.git
cd finagun
pip install yfinance matplotlib
```

## 使い方

```bash
python finagun.py        # 資産管理
python fina_bktest.py    # バックテスト
python fina_gather.py    # 銘柄情報の取得
```

## 免責事項

本プロジェクトは情報提供・学習を目的としており、投資助言ではありません。投資判断は自己責任で行ってください。

## ライセンス

[MIT License](LICENSE)
