# fina_asset.py 設計書 v0.1

作成：2026-11-18 / 元にした仕様：[fina_asset_spec.md](fina_asset_spec.md)（v0.1）

この設計書は、仕様メモの決定事項を「どのファイルに・どんなクラスで・どんなデータで」実装するかに落としたもの。
Phase 1（日本株現物）は細かく決め、Phase 2 以降は **差し替え口（拡張ポイント）だけ先に用意しておく** 方針にする。

Phase 2 の詳しい設計は [fina_asset_design_phase2.md](fina_asset_design_phase2.md) にある（この設計書の §4.6・§5・§6 より、そちらを優先する）。

---

## 1. ファイル構成

CLAUDE.md の接頭語ルールに合わせ、画面と計算を分ける。

| ファイル | 種類 | 内容 | Phase |
|---|---|---|---|
| `fina_asset.py` | 主要 | 資産管理の画面（タブ：保有一覧・取引履歴・実現損益 ほか）。取引入力ダイアログもここに置く | 1 |
| `core_asset.py` | 計算 | データの型（商品・取引・保有）、JSON の読み書き、取引からの保有集計（移動平均法）、実現損益 | 1 |
| `core_asset_value.py` | 計算 | 価格ソースと、資産クラスごとの評価（評価額・含み損益）、資産推移スナップショット | 1 |
| `core_asset_import.py` | 計算 | 取引履歴 CSV の取り込み。証券会社ごとのアダプタ（まず楽天証券） | 2 |
| `core_dividend.py` | 計算 | yfinance から配当情報を取得し、JSON にキャッシュする | 2 |
| `gui_treemap.py` | フォーム | ツリーマップ（ヒートマップ）の描画部品。他のプログラムでも使えるように分ける | 2 |

- `core_` の 3 ファイルは tkinter を使わない（単体でテスト・CLI から使える）。
- 追加したら README.md のプログラム一覧に追記する（`fina_asset.py` はすでに載っている）。

### 保存ファイル（ルートディレクトリ・git 管理外）

| ファイル | 内容 |
|---|---|
| `asset.json` | 商品マスタ・取引・証券会社の一覧・資産推移スナップショット・画面の状態 |
| `dividend_cache.json` | 配当情報のキャッシュ（Phase 2） |

- `.gitignore` に `asset.json` / `asset.json.tmp` / `asset.json.bak` / `dividend_cache.json` を追加する。
- 書き込みは `fina_watchlist.py` と同じく「`.tmp` に書いて `os.replace`」。壊れていたら `.bak` に退避して空で起動する。
- 将来 SQLite へ移すため、**読み書きは `AssetStore` クラスだけが行う**（画面や計算は JSON を直接触らない）。

---

## 2. データモデル（core_asset.py）

### 2.1 資産クラス

```python
class AssetClass(StrEnum):
    JP_STOCK = "jp_stock"   # 日本株現物(ETF・REIT を含む)   Phase 1
    US_STOCK = "us_stock"   # 米国株                          Phase 3
    FUND     = "fund"       # 投資信託                        Phase 3
    FX       = "fx"         # FX                              Phase 4
    OPTION   = "option"     # オプション(日経225・米国)       Phase 4
```

### 2.2 商品マスタ `Instrument`

```python
@dataclass(frozen=True)
class Instrument:
    id: str                  # 商品の ID(下の表の形式)。取引からはこの ID で参照する
    asset_class: AssetClass
    code: str                # 銘柄コード・ティッカー・通貨ペアなど
    name: str
    currency: str = "JPY"    # 価格の通貨
    market: str = ""         # 市場(プライム、NASDAQ など)
    sector: str = ""         # セクター(日本株は銘柄一覧の 33 業種区分)
    # --- オプション用 ---
    underlying: str = ""     # 原資産(N225、AAPL など)
    right: str = ""          # "call" / "put"
    strike: float | None = None
    expiry: str = ""         # 限月 "2026-12"
    multiplier: float = 1    # 倍率(日経225オプション 1000、米国オプション 100)
    # --- FX 用 ---
    pair: str = ""           # "USDJPY" など
```

| 資産クラス | ID の形式 | 例 |
|---|---|---|
| 日本株 | `JP:<コード>` | `JP:7203` |
| 米国株 | `US:<ティッカー>` | `US:AAPL` |
| 投信 | `FUND:<協会コード>` | `FUND:0331418A` |
| FX | `FX:<通貨ペア>` | `FX:USDJPY` |
| オプション | `OPT:<原資産>:<C/P>:<行使価格>:<限月>` | `OPT:N225:C:40000:2026-12` |

- 日本株は、取引入力で銘柄を選んだときに銘柄一覧（`core_stocks.load_listed()`）から名称・市場・33 業種を写して自動登録する。利用者がマスタを手で編集する必要はない。

### 2.3 取引 `Trade`（データの中心）

```python
@dataclass
class Trade:
    id: int                  # 通し番号(作成順。同じ日の取引の順番にも使う)
    date: str                # 約定日 "YYYY-MM-DD"
    instrument_id: str
    broker: str              # 証券会社(「楽天証券」など。asset.json の brokers から選ぶ)
    account: str             # 口座区分(下の一覧)
    action: str              # "buy" / "sell" / "split"(株式分割) / "swap"(FX のスワップ、Phase 4)
    quantity: float          # 数量(株数・口数・通貨数量・枚数)。常に正の数
    price: float             # 約定単価(商品の通貨)。split では分割比率(1株→2株なら 2)
    fee: float = 0           # 手数料(税込み・円)
    time: str = ""           # 約定時刻 "HH:MM"(任意。仕様の「日時」の時刻部分)
    fx_rate: float | None = None  # 約定時の為替レート(外貨建て商品のみ、Phase 3)
    memo: str = ""
    source: str = "manual"   # "manual" / "csv:rakuten" など
    import_key: str = ""     # CSV 取り込みの重複判定用(Phase 2)
```

口座区分（固定の一覧。`ACCOUNT_TYPES`）：`特定` / `一般` / `NISA成長` / `NISAつみたて` / `旧NISA`

**設計で決めたこと（仕様に書かれていない部分）**

- **株式分割**：`stock_database` の CSV（yfinance）は分割で過去の価格が調整されるため、保有数量も分割に合わせる必要がある。`action="split"` を「取引の一種」として記録し、数量 × 比率、平均単価 ÷ 比率 に直す。
- **時刻は任意**：日足ベースなので並び順は `(date, time, id)` で決める。同じ日の買いと売りは入力順。
- **売りは常に正の数量**で記録する（符号で売買を表さない）。ショート（売り建て）は資産クラスの規則で許すかを決める（2.5）。

### 2.4 保有 `Holding` と実現損益 `Realized`（取引から集計。保存しない）

```python
HoldingKey = tuple[str, str, str]   # (instrument_id, broker, account)

@dataclass
class Holding:
    key: HoldingKey
    quantity: float          # 正ならロング、負ならショート(FX・オプションのみ)
    cost: float              # 取得原価の合計(商品の通貨。買い手数料を含む)
    @property
    def average_price(self) -> float: ...   # cost / quantity

@dataclass(frozen=True)
class Realized:
    trade_id: int            # 決済した取引
    date: str
    key: HoldingKey
    quantity: float
    proceeds: float          # 売却代金 - 売り手数料
    cost: float              # 平均単価 × 数量
    @property
    def profit(self) -> float: ...          # proceeds - cost(税引前)
```

### 2.5 集計：移動平均法 `build_ledger(trades, instruments) -> Ledger`

取引を `(date, time, id)` 順に 1 件ずつ処理し、`HoldingKey` ごとに数量と原価を更新する。

| 取引 | 処理 |
|---|---|
| 買い（同じ向きの追加・新規） | `cost += quantity × price × multiplier + fee`、`quantity += 数量` |
| 売り（反対向き＝決済） | 平均単価で原価を取り崩す。`Realized(proceeds = 数量×価格×倍率 − fee, cost = 平均単価×数量)` を記録 |
| 数量を超える決済 | 現物株：**エラー**（入力時に弾く）。FX・オプション：残りを反対向きの新規建てとする |
| 分割 | `quantity ×= 比率`（原価はそのまま → 平均単価は自動で ÷ 比率） |
| 数量が 0 になった保有 | 一覧から消す（原価も 0 に戻す） |

- 計算は float で持ち、表示のときに丸める。税制上の「円未満切り上げ」などは行わない（税は反映しない方針）。
- `Ledger` は `holdings: dict[HoldingKey, Holding]` と `realized: list[Realized]` を持つ。
- 取引を 1 件追加・編集・削除するたびに **全件から作り直す**（個人の取引件数なら一瞬。差分更新の不具合を避ける）。
- 入力チェック `validate_trade(trade, trades) -> list[str]` で、その取引を入れたときに途中で数量がマイナスにならないかを確かめる（過去の日付に売りを入れた場合も含めて全件で再計算して判定）。

資産クラスごとの違いは規則テーブルにまとめる：

```python
@dataclass(frozen=True)
class AssetClassRule:
    label: str               # 「日本株」など
    allow_short: bool        # 売り建てを許すか
    unit_label: str          # 数量の単位「株」「口」「枚」「通貨」

ASSET_CLASS_RULES = {
    AssetClass.JP_STOCK: AssetClassRule("日本株", False, "株"),
    AssetClass.US_STOCK: AssetClassRule("米国株", False, "株"),
    AssetClass.FUND:     AssetClassRule("投資信託", False, "口"),
    AssetClass.FX:       AssetClassRule("FX", True, "通貨"),
    AssetClass.OPTION:   AssetClassRule("オプション", True, "枚"),
}
```

### 2.6 保存 `AssetStore`

```python
class AssetStore:
    def __init__(self, path: Path = ASSET_PATH): ...
    def load(self) -> None                       # 壊れた要素は飛ばして読む(parse_* 関数で検査)
    def save(self) -> None                       # .tmp に書いて置き換える
    instruments: dict[str, Instrument]
    trades: list[Trade]
    brokers: list[str]                           # 証券会社の一覧(利用者が追加・改名できる)
    snapshots: list[Snapshot]
    ui: dict                                     # 絞り込み・列幅・選択タブなどの画面の状態
    def add_trade(self, trade) / update_trade(self, trade) / delete_trade(self, trade_id)
    def ensure_instrument(self, instrument) -> None
```

`asset.json` の形：

```json
{
  "version": 1,
  "brokers": ["楽天証券", "SBI証券"],
  "instruments": {
    "JP:7203": {"asset_class": "jp_stock", "code": "7203", "name": "トヨタ自動車",
                "currency": "JPY", "market": "プライム", "sector": "輸送用機器"}
  },
  "trades": [
    {"id": 1, "date": "2026-11-10", "instrument_id": "JP:7203", "broker": "楽天証券",
     "account": "特定", "action": "buy", "quantity": 100, "price": 2850, "fee": 0}
  ],
  "snapshots": [
    {"date": "2026-11-18", "price_date": "2026-11-17", "value": 285000, "cost": 285000,
     "unrealized": 0, "realized_total": 0, "by_class": {"jp_stock": 285000}}
  ],
  "ui": {"tab": "holdings", "broker": "", "account": ""}
}
```

- 値が既定値の項目（オプション用の空欄など）は書き出さない（ファイルを読みやすくする）。
- `next_id` は保存せず、読み込み時に `max(id) + 1` とする。

---

## 3. 評価（core_asset_value.py）

### 3.1 価格ソース

```python
@dataclass(frozen=True)
class Quote:
    date: str                # 価格の日付(前日終値の日)
    close: float
    prev_close: float | None # その前の営業日の終値(前日比用)

class PriceSource(Protocol):
    def quote(self, instrument: Instrument) -> Quote | None: ...
    def history(self, instrument: Instrument) -> pd.Series | None: ...  # 期間騰落(1週・1月・年初来)用
```

| 実装 | 対象 | 中身 | Phase |
|---|---|---|---|
| `LocalJpStockPrices` | 日本株 | `core_gather.load_saved(csv_path_for(code))` の **終値（Close）だけ** を使う。読み込んだ結果は銘柄ごとにキャッシュ | 1 |
| `YFinancePrices` | 米国株・ドル円・FX | yfinance で取得し、ローカルにキャッシュ | 3〜4 |
| （未定） | 投信の基準価額、オプション清算値 | 価格ソースを追加 | 3〜4 |

- 「前日終値ベース」＝ CSV の最終行の終値。CSV の更新は従来どおり `core_gather.py` で行い、fina_asset は株価を取得しない（fina_watchlist と同じ）。
- 価格がない銘柄（CSV なし・上場廃止）は評価額を「―」にし、合計から除いて画面に警告を出す。

### 3.2 評価器（資産クラスごとに差し替え）

```python
@dataclass(frozen=True)
class Valuation:
    holding: Holding
    quote: Quote | None
    value_jpy: float | None        # 評価額(円)
    unrealized_jpy: float | None   # 含み損益(円)
    change_pct: float | None       # 前日比(%)

class Valuer(Protocol):
    def value(self, holding: Holding, instrument: Instrument, prices: PriceSource) -> Valuation: ...

VALUERS: dict[AssetClass, Valuer] = {
    AssetClass.JP_STOCK: StockValuer(),     # 数量 × 終値                    Phase 1
    AssetClass.FUND:     FundValuer(),      # 口数 × 基準価額 ÷ 10000        Phase 3
    AssetClass.US_STOCK: ForeignStockValuer(), # ドル価格 × ドル円終値        Phase 3
    AssetClass.FX:       FxValuer(),        # (終値 − 平均建値) × 建玉 + スワップ  Phase 4
    AssetClass.OPTION:   OptionValuer(),    # 数量 × 清算値 × 倍率            Phase 4
}

def value_all(ledger: Ledger, instruments, prices) -> list[Valuation]
def summarize(valuations, realized) -> Summary   # 合計評価額・含み損益・実現損益(全期間/年初来)
```

- 新しい資産クラスは `AssetClass`・`ASSET_CLASS_RULES`・`VALUERS` に 1 行ずつ足すだけで載るようにする。
- 外貨建ての含み損益（Phase 3）：取得原価は約定時の `fx_rate` で円換算し、評価額は前日のドル円で換算する。`fx_rate` が空の取引は約定日のドル円終値で補う。

### 3.3 資産推移スナップショット

```python
@dataclass(frozen=True)
class Snapshot:
    date: str            # 記録した日(アプリを起動した日)
    price_date: str      # 評価に使った価格の日付
    value: float
    cost: float
    unrealized: float
    realized_total: float
    by_class: dict[str, float]

def take_snapshot_if_needed(store, ledger, prices, today) -> bool
```

- `fina_asset.py` の起動時に呼び、`snapshots` に今日の日付がなければ 1 件追加して保存する（仕様どおり 1 日 1 回、起動しなかった日はなし）。
- 保有がない日も 0 として記録する（推移グラフが途切れないように）。

---

## 4. 画面（fina_asset.py）

### 4.1 全体のレイアウト

```
┌──────────────────────────────────────────────────────────────┐
│ 証券会社[すべて▼] 口座区分[すべて▼] 資産クラス[すべて▼]  [取引を入力] │ ← 共通の絞り込み
├──────────────────────────────────────────────────────────────┤
│ [保有一覧][取引履歴][実現損益][集計][配当][ヒートマップ]            │ ← ttk.Notebook
│                                                              │
│                     (タブの中身)                              │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ 評価額 1,234,567円  含み損益 +45,678円(+3.8%)  実現損益(年初来) +12,345円 │
│ 価格: 2026-11-17 終値   ※ 2 銘柄は株価データがありません            │ ← 合計とお知らせ
└──────────────────────────────────────────────────────────────┘
```

- 絞り込みは全タブ共通。変えると全タブと下の合計を作り直す。
- クラス構成：

| クラス | 役割 |
|---|---|
| `AssetApp(ttk.Frame)` | 全体。`AssetStore`・`Ledger`・`PriceSource` を持ち、変更があれば `refresh()` で各タブに配る |
| `HoldingsTab` | 保有一覧 |
| `TradesTab` | 取引履歴（一覧・編集・削除） |
| `RealizedTab` | 実現損益の一覧（決済ごと）と、年別・銘柄別の小計 |
| `TradeDialog(tk.Toplevel)` | 取引の入力・編集フォーム |
| `BrokerDialog` | 証券会社の追加・改名・削除 |
| `SummaryTab`・`DividendTab`・`HeatmapTab` | Phase 2 |

データの流れは一方向にする：
`TradeDialog で保存` → `AssetStore` を更新・保存 → `build_ledger()` → `value_all()` → 各タブの `show(...)`

### 4.2 保有一覧（Phase 1）

ttk.Treeview。列：

| 列 | 内容 |
|---|---|
| コード / 銘柄名 | |
| 証券会社 / 口座 | 同じ銘柄でも (証券会社 × 口座区分) ごとに別の行 |
| 数量 | |
| 平均取得単価 | 移動平均法 |
| 現在値 | 前日終値 |
| 前日比 | % |
| 評価額 | |
| 含み損益 / 損益率 | 日本式の色：利益は赤、損失は緑（設定で反転可） |

- 列見出しクリックで並べ替え。
- 「銘柄ごとにまとめる」チェック：ON なら証券会社・口座をまたいで合算した行にする（平均単価は原価合計 ÷ 数量合計）。
- **ダブルクリック／「チャート」ボタン**：`tk.Toplevel` に `gui_chart.ChartFrame(win, code)` を開き、その銘柄の自分の取引を `set_trades([TradeMark(...)])` で表示する（買い→売りの組は移動平均法では対応しないので、買いごとの点として出し、決済済み分は売りの日付・価格を exit に入れる）。
- 右クリック：「この銘柄を買い増し／売却」（TradeDialog を銘柄・証券会社・口座を埋めた状態で開く）、「ウォッチリストに追加」（Phase 2、`watchlist.json` のグループを選ぶ）。

### 4.3 取引入力 `TradeDialog`（Phase 1）

```
┌ 取引の入力 ──────────────────────────────┐
│ 約定日   [2026-11-18]  時刻[     ](任意)   │
│ 銘柄     [StockSearchBox: トヨタ        ]  │ → 7203 トヨタ自動車
│ 証券会社 [楽天証券 ▼] [編集…]              │
│ 口座区分 [特定 ▼]                         │
│ 売買     (●買い ○売り ○分割)               │
│ 数量     [100    ] 株   (保有: 200 株)      │
│ 単価     [2,850  ] 円   (前日終値: 2,840)   │
│ 手数料   [0      ] 円                     │
│ メモ     [                          ]     │
│ 受渡金額  285,000 円                       │
│              [保存して次へ] [保存] [取消]   │
└────────────────────────────────────────┘
```

- 銘柄は `gui_widgets.StockSearchBox` を使う（銘柄一覧にないコードは `on_unmatched` で受け、名称を手入力させる）。
- 証券会社・口座区分は、前回入力した値を初期値にする（`ui` に保存）。
- 「保有」「前日終値」を横に出して入力ミスを減らす。
- 保存時に `validate_trade` を通し、問題があればダイアログ内に赤字で出す（保存しない）。
- 「保存して次へ」は日付・証券会社・口座を残して他を空にする（まとめて入力しやすく）。
- 資産クラスの選択欄は Phase 3 で追加する（Phase 1 は日本株固定）。

### 4.4 取引履歴（Phase 1）

- 一覧：約定日・コード・銘柄名・証券会社・口座・売買・数量・単価・手数料・受渡金額・メモ・入力元。
- 期間・銘柄（StockSearchBox）での絞り込み。
- ダブルクリックで `TradeDialog` を編集モードで開く。Delete キー／「削除」ボタンで確認して削除。
- 編集・削除で過去の数量がマイナスになる場合は保存させない（理由を表示）。

### 4.5 実現損益（Phase 1）

- 決済ごとの一覧：日付・銘柄・証券会社・口座・数量・売却額・取得原価・損益。
- 上に「年」選択（すべて／2026／2025…）と、年合計・銘柄別小計。税引前で統一。

### 4.6 Phase 2 以降の画面

| タブ | 中身 | 使うもの |
|---|---|---|
| 集計 | 資産推移の折れ線（評価額と原価の 2 本）、配分の円グラフ（銘柄別・33 業種別・資産クラス別・証券会社別を切り替え） | matplotlib、`snapshots` |
| 配当 | 保有銘柄の 1 株配当・権利落ち日・支払日・保有数量 × 配当の見込み額。月ごとの一覧（カレンダー風） | `core_dividend.py` |
| ヒートマップ | 面積＝評価額、色＝前日比／含み損益率／1週・1月・年初来騰落。グループ＝セクター／資産クラス／証券会社。赤＝上昇がデフォルト | `gui_treemap.py` |

---

## 5. CSV 取り込み（core_asset_import.py、Phase 2）

```python
@dataclass
class ImportRow:
    trade: Trade | None      # 変換できた取引(id は未採番)
    instrument: Instrument | None
    error: str = ""          # 変換できなかった理由(その行は取り込まない)
    duplicate: bool = False  # すでに取り込み済み

class BrokerCsvAdapter(ABC):
    key: str                 # "rakuten" など(Trade.source = "csv:" + key)
    label: str               # 「楽天証券」
    encoding: str = "cp932"
    @abstractmethod
    def detect(self, header: list[str]) -> bool: ...       # 見出し行から自社の形式か判定
    @abstractmethod
    def parse(self, rows: list[dict[str, str]]) -> list[ImportRow]: ...

ADAPTERS: list[BrokerCsvAdapter] = [RakutenJpStockAdapter()]

def read_csv(path: Path, adapter: BrokerCsvAdapter | None = None) -> list[ImportRow]
    # adapter 省略時は detect() で自動判定
```

- 重複判定：`import_key` ＝ 約定日・銘柄・売買・数量・単価・口座区分・同じ内容の何件目か、を連結した文字列。同じ CSV を 2 回取り込んでも増えない。
- 画面：ファイルを選ぶ → プレビュー（取り込む行／重複／エラーを色分け）→ 確定。
- 楽天証券の CSV の列名は、実際のファイルを見てから決める（**要確認**：国内株式の「取引履歴」CSV の見出しと文字コード）。
- 他社はアダプタのクラスを 1 つ足して `ADAPTERS` に加えるだけ。

## 6. 配当情報（core_dividend.py、Phase 2）

```python
@dataclass(frozen=True)
class DividendInfo:
    code: str
    history: list[tuple[str, float]]  # (権利落ち日, 1株配当) Ticker.dividends
    next_ex_date: str | None          # Ticker.calendar
    next_pay_date: str | None
    fetched_at: str

def load_dividends(codes, refresh_days=7) -> dict[str, DividendInfo]
```

- 対象は保有銘柄 ＋ `watchlist.json` の銘柄だけ。日本株は `<コード>.T`。
- キャッシュが `refresh_days` 日より古い銘柄だけ取得する。1 銘柄ずつ少し間を空けて取得（レート制限対策）。
- 取得は時間がかかるので、画面からは **別スレッドで実行し、`after()` で結果を受け取る**（画面を固めない）。
- 権利確定日は yfinance から取れないため、表示は「権利落ち日」を基準にする（権利確定日は権利落ち日の翌営業日として参考表示）。

## 7. 他のプログラムとの連携

| 連携 | 方法 |
|---|---|
| 保有一覧 → チャート | `gui_chart.ChartFrame` を Toplevel で開く（4.2） |
| 保有一覧 → ウォッチリスト | `fina_watchlist.read_watchlist / write_watchlist` で指定グループにコードを追加（Phase 2） |
| スクリーニング結果 → 監視リスト | `fina_screening.py` 側の改修（このファイルの範囲外。同じ関数を使う） |
| ToreCan.py（ホーム画面） | `AssetApp` を Frame として作っておけば、ホーム画面のタブにも埋め込める |

---

## 8. 実装の順番（Phase 1）

1. `core_asset.py`：型・`AssetStore`・`build_ledger`・`validate_trade`
   - 移動平均法のテスト（買い増し・一部売却・全売却・分割・同日売買・過去日付への売り挿入）を `if __name__ == "__main__"` か簡単なテストで確認
2. `core_asset_value.py`：`LocalJpStockPrices`・`StockValuer`・`summarize`・スナップショット
3. `fina_asset.py`：保有一覧 → 取引入力 → 取引履歴 → 実現損益 → チャート連携
4. README.md・.gitignore の更新

## 9. 決めておきたい点（設計で仮に決めたもの）

仕様メモにない部分は、次のように仮決めしている。違っていれば直す。

| 項目 | 仮の決定 |
|---|---|
| 買い手数料 | 取得原価に含める（平均単価が上がる）。売り手数料は売却代金から引く |
| 株式分割 | 取引の一種（`split`）として手入力する |
| 現物株の売り超過 | エラーにして保存させない |
| NISA の実現損益 | 他の口座と同じように計算・表示する（税を反映しないため区別不要） |
| 証券会社間の移管・入庫 | Phase 1 では扱わない（必要なら「売り＋買い」を手数料 0・同価格で入力） |
| スナップショットの日付 | 起動した日。評価に使った価格の日付も別に持つ |
