"""保有の評価(評価額・含み損益)と、資産推移のスナップショット(GUI を含まない部分)。

設計は fina_asset_design.md を参照。
価格は前日終値ベース。日本株は stock_database フォルダの保存済み日足 CSV の終値(Close)だけを使う
(株価は取得しない。CSV の更新は core_gather.py で行う)。

評価の方法は資産クラスごとに VALUERS で差し替える。
新しい資産クラスに対応するときは、価格ソースと評価器を作って VALUERS・PRICE_SOURCES に足す。

使い方(他のプログラムから):
    prices = LocalJpStockPrices()
    valuations = value_all(ledger, store.instruments, prices)
    summary = summarize(valuations, ledger.realized, today="2026-11-18")
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from core_asset import (
    ASSET_CLASS_RULES, AssetClass, AssetStore, Holding, HoldingKey, Instrument, Ledger, Realized, Snapshot, Trade,
    build_ledger,
)
from core_gather import csv_path_for, load_saved


@dataclass(frozen=True)
class Quote:
    """評価に使う価格。"""
    date: str                 # 価格の日付(前日終値の日)
    close: float
    prev_close: float | None  # その前の営業日の終値(前日比用)

    @property
    def change_pct(self) -> float | None:
        """前日比(%)。"""
        if not self.prev_close:
            return None
        return (self.close / self.prev_close - 1) * 100


class PriceSource(Protocol):
    """価格ソース。"""

    def quote(self, instrument: Instrument) -> Quote | None: ...

    def history(self, instrument: Instrument) -> pd.Series | None: ...


class LocalJpStockPrices:
    """日本株の価格(stock_database フォルダの保存済み日足 CSV の終値)。読み込んだ終値は銘柄ごとに覚えておく。"""

    def __init__(self):
        self.closes: dict[str, pd.Series | None] = {}

    def history(self, instrument: Instrument) -> pd.Series | None:
        """終値の系列(日付が行の名前)。データがなければ None。"""
        code = instrument.code
        if code not in self.closes:
            try:
                daily = load_saved(csv_path_for(code))
                close = daily["Close"].dropna() if daily is not None else None
            except (OSError, ValueError, KeyError):  # 壊れた CSV は、データなしとして扱う
                close = None
            self.closes[code] = close if close is not None and len(close) > 0 else None
        return self.closes[code]

    def quote(self, instrument: Instrument) -> Quote | None:
        close = self.history(instrument)
        if close is None:
            return None
        prev = float(close.iloc[-2]) if len(close) > 1 else None
        return Quote(f"{close.index[-1]:%Y-%m-%d}", float(close.iloc[-1]), prev)

    def clear(self) -> None:
        """覚えている価格を捨てる(CSV を更新したあとに読み直す)。"""
        self.closes.clear()


# ---- 評価 ----

@dataclass(frozen=True)
class Valuation:
    """保有 1 件の評価。価格がなければ value_jpy などは None。"""
    holding: Holding
    instrument: Instrument
    quote: Quote | None
    value_jpy: float | None       # 評価額(円)
    cost_jpy: float               # 取得原価(円)
    unrealized_jpy: float | None  # 含み損益(円)
    note: str = ""                # 評価できなかった理由など

    @property
    def unrealized_pct(self) -> float | None:
        """含み損益率(%)。"""
        if self.unrealized_jpy is None or not self.cost_jpy:
            return None
        return self.unrealized_jpy / self.cost_jpy * 100

    @property
    def change_pct(self) -> float | None:
        return self.quote.change_pct if self.quote else None


class Valuer(Protocol):
    """資産クラスごとの評価器。"""

    def value(self, holding: Holding, instrument: Instrument, prices: PriceSource) -> Valuation: ...


class StockValuer:
    """現物株(円建て)。評価額 = 数量 × 前日終値。"""

    def value(self, holding: Holding, instrument: Instrument, prices: PriceSource) -> Valuation:
        quote = prices.quote(instrument)
        if quote is None:
            return Valuation(holding, instrument, None, None, holding.cost, None, "株価データがありません")
        value = holding.quantity * quote.close * holding.multiplier
        return Valuation(holding, instrument, quote, value, holding.cost, value - holding.cost)


class UnsupportedValuer:
    """まだ評価に対応していない資産クラス。"""

    def value(self, holding: Holding, instrument: Instrument, prices: PriceSource) -> Valuation:
        return Valuation(holding, instrument, None, None, holding.cost, None, "この資産クラスの評価は未対応です")


# 資産クラス -> 評価器。Phase 3 以降で FundValuer・ForeignStockValuer・FxValuer・OptionValuer を足す
VALUERS: dict[AssetClass, Valuer] = {
    AssetClass.JP_STOCK: StockValuer(),
}


class PriceBook:
    """資産クラスごとの価格ソースをまとめたもの(評価器には、商品に合った価格ソースを渡す)。"""

    def __init__(self, sources: dict[AssetClass, PriceSource] | None = None):
        self.sources = sources if sources is not None else {AssetClass.JP_STOCK: LocalJpStockPrices()}

    def quote(self, instrument: Instrument) -> Quote | None:
        source = self.sources.get(instrument.asset_class)
        return source.quote(instrument) if source else None

    def history(self, instrument: Instrument) -> pd.Series | None:
        source = self.sources.get(instrument.asset_class)
        return source.history(instrument) if source else None

    def clear(self) -> None:
        for source in self.sources.values():
            if hasattr(source, "clear"):
                source.clear()


def value_all(ledger: Ledger, instruments: dict[str, Instrument], prices: PriceSource) -> list[Valuation]:
    """保有をすべて評価する。"""
    valuations = []
    for holding in ledger.holdings.values():
        instrument = instruments[holding.instrument_id]
        valuer = VALUERS.get(instrument.asset_class, UnsupportedValuer())
        valuations.append(valuer.value(holding, instrument, prices))
    return valuations


@dataclass(frozen=True)
class Summary:
    """合計。"""
    value: float           # 評価額の合計(価格のある保有だけ)
    cost: float            # 取得原価の合計(価格のある保有だけ)
    unrealized: float      # 含み損益の合計
    realized_total: float  # 実現損益の累計
    realized_ytd: float    # 実現損益(今年)
    price_date: str        # 評価に使った価格の日付(もっとも新しいもの)
    missing: list[Valuation] = field(default_factory=list)  # 評価できなかった保有
    by_class: dict[str, float] = field(default_factory=dict)
    by_group: dict[str, list[float]] = field(default_factory=dict)  # "証券会社|口座区分" -> [評価額, 取得原価]

    @property
    def unrealized_pct(self) -> float | None:
        return self.unrealized / self.cost * 100 if self.cost else None


def summarize(valuations: list[Valuation], realized: list[Realized], today: str) -> Summary:
    """評価と実現損益を合計する。today("YYYY-MM-DD")は今年の実現損益を出すのに使う。"""
    priced = [v for v in valuations if v.value_jpy is not None]
    by_class: dict[str, float] = {}
    by_group: dict[str, list[float]] = {}
    for v in priced:
        by_class[v.instrument.asset_class] = by_class.get(v.instrument.asset_class, 0.0) + v.value_jpy
        group = by_group.setdefault(group_key(v.holding.broker, v.holding.account), [0.0, 0.0])
        group[0] += v.value_jpy
        group[1] += v.cost_jpy
    return Summary(
        value=math.fsum(v.value_jpy for v in priced),
        cost=math.fsum(v.cost_jpy for v in priced),
        unrealized=math.fsum(v.unrealized_jpy for v in priced),
        realized_total=math.fsum(r.profit for r in realized),
        realized_ytd=math.fsum(r.profit for r in realized if r.date[:4] == today[:4]),
        price_date=max((v.quote.date for v in priced), default=""),
        missing=[v for v in valuations if v.value_jpy is None],
        by_class=by_class,
        by_group=by_group,
    )


def group_key(broker: str, account: str) -> str:
    """スナップショットの内訳のキー。"""
    return f"{broker}|{account}"


# ---- 資産推移のスナップショット ----

def record_snapshot(store: AssetStore, summary: Summary, today: str) -> bool:
    """今日の合計(全保有。絞り込みなし)を記録する。記録を変えたら True(保存は呼び出し側で行う)。

    記録は 1 日 1 件。アプリの起動時と、取引を変えて集計し直したときに呼び、今日の記録を最新の値で置き換える
    (起動したあとに取引を入力・取り込みしても、今日の記録が古い値のまま残らないように)。
    過ぎた日の記録は変えない。起動しなかった日は記録しない。保有がなくても 0 として記録する。
    """
    snapshot = Snapshot(
        date=today,
        price_date=summary.price_date,
        value=round(summary.value, 2),
        cost=round(summary.cost, 2),
        unrealized=round(summary.unrealized, 2),
        realized_total=round(summary.realized_total, 2),
        by_class={k: round(v, 2) for k, v in summary.by_class.items()},
        by_group={k: [round(v[0], 2), round(v[1], 2)] for k, v in summary.by_group.items()},
    )
    if snapshot in store.snapshots:
        return False
    store.snapshots = sorted([*(s for s in store.snapshots if s.date != today), snapshot], key=lambda s: s.date)
    return True


# ---- 配分・期間騰落率・過去の推移(Phase 2) ----

# 配分の分け方 -> 表示名
ALLOCATION_KINDS = {
    "stock": "銘柄",
    "sector": "33業種",
    "asset_class": "資産クラス",
    "broker": "証券会社",
    "account": "口座区分",
}


def allocation(valuations: list[Valuation], kind: str) -> list[tuple[str, float]]:
    """評価額の配分を、大きい順の (項目名、評価額) の一覧にする。kind は ALLOCATION_KINDS のどれか。"""
    totals: dict[str, float] = {}
    for v in valuations:
        if v.value_jpy is None:
            continue
        if kind == "stock":
            label = f"{v.instrument.code} {v.instrument.name}".strip()
        elif kind == "sector":
            label = v.instrument.sector or "(業種なし)"
        elif kind == "asset_class":
            label = ASSET_CLASS_RULES[v.instrument.asset_class].label
        elif kind == "broker":
            label = v.holding.broker
        else:
            label = v.holding.account
        totals[label] = totals.get(label, 0.0) + v.value_jpy
    return sorted(totals.items(), key=lambda item: -item[1])


def period_change(history: pd.Series | None, days: int | None, today: str) -> float | None:
    """days 日前(その日に値がなければ、その前の最後の終値)から最新の終値までの騰落率(%)。

    days が None なら年初来(前の年の最後の終値から)。比べる値がなければ None。
    """
    if history is None or len(history) == 0:
        return None
    if days is None:
        base_date = pd.Timestamp(f"{today[:4]}-01-01") - pd.Timedelta(days=1)
    else:
        base_date = history.index[-1] - pd.Timedelta(days=days)
    before = history[history.index <= base_date]
    if len(before) == 0 or not before.iloc[-1]:
        return None
    return (float(history.iloc[-1]) / float(before.iloc[-1]) - 1) * 100


def split_factors(trades: list[Trade]) -> dict[str, list[tuple[str, float]]]:
    """商品 ID -> [(分割の日、比率)]。同じ銘柄の分割は、保有ごとに入力されていても 1 回と数える。"""
    result: dict[str, dict[str, float]] = {}
    for t in trades:
        if t.action == "split" and t.price > 0:
            result.setdefault(t.instrument_id, {})[t.date] = t.price
    return {k: sorted(v.items()) for k, v in result.items()}


def rebuild_history(
    trades: list[Trade], instruments: dict[str, Instrument], prices: PriceSource,
    matches: Callable[[HoldingKey], bool] | None = None,
) -> pd.DataFrame:
    """取引の記録と保存済みの日足から、過去の営業日ごとの評価額と取得原価を計算し直す。

    列は value(評価額)・cost(取得原価)、行は日付。価格のない銘柄は入れない。
    保存済みの日足は分割に合わせて過去の値が直されているので、分割より前の日は 比率 を掛けて戻す。
    matches を渡すと、その保有(キー)だけを数える(証券会社・口座区分の絞り込み)。
    """
    states: list[tuple[pd.Timestamp, dict[HoldingKey, tuple[float, float]]]] = []

    def record(date: str, holdings: dict[HoldingKey, Holding]) -> None:
        states.append((pd.Timestamp(date), {
            key: (h.quantity, h.cost) for key, h in holdings.items()
            if h.quantity != 0 and (matches is None or matches(key))
        }))

    build_ledger(trades, instruments, on_date=record)
    if not states:
        return pd.DataFrame(columns=["value", "cost"])

    used = {key[0] for _, state in states for key in state}
    closes = {}
    for instrument_id in used:
        history = prices.history(instruments[instrument_id])
        if history is not None:
            closes[instrument_id] = history
    if not closes:
        return pd.DataFrame(columns=["value", "cost"])
    start = states[0][0]
    dates = sorted({d for s in closes.values() for d in s.index if d >= start})
    if not dates:
        return pd.DataFrame(columns=["value", "cost"])
    index = pd.DatetimeIndex(dates)
    splits = split_factors(trades)
    adjusted = {}
    for instrument_id, history in closes.items():
        close = history.reindex(index.union(history.index)).ffill().reindex(index)
        # 分割より前の日は、調整済みの株価に比率を掛けて、当時の数量に合う株価に戻す
        factor = pd.Series(1.0, index=index)
        for split_date, ratio in splits.get(instrument_id, []):
            factor[index < pd.Timestamp(split_date)] *= ratio
        adjusted[instrument_id] = (close * factor).to_numpy()

    values, costs = [], []
    state_index = 0
    current: dict[HoldingKey, tuple[float, float]] = {}
    for i, day in enumerate(index):
        while state_index < len(states) and states[state_index][0] <= day:
            current = states[state_index][1]
            state_index += 1
        value = cost = 0.0
        for key, (quantity, key_cost) in current.items():
            series = adjusted.get(key[0])
            if series is None or math.isnan(series[i]):
                continue
            value += quantity * series[i] * instruments[key[0]].multiplier
            cost += key_cost
        values.append(value)
        costs.append(cost)
    return pd.DataFrame({"value": values, "cost": costs}, index=index)
