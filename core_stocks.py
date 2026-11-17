"""銘柄一覧の読み込みと、銘柄の検索(GUI を含まない部分)。

listed_stocks フォルダの最新の銘柄一覧 CSV(core_listup_stocks.py で生成)を読み込む。
銘柄の検索では、銘柄コードまたは銘柄名の一部で銘柄を探す。
全角・半角、大文字・小文字、ひらがな・カタカナは区別しない。

検索の入力欄と候補の一覧(tkinter の部品)は、gui_widgets.py の StockSearchBox にある。

使い方(他のプログラムから):
    listed = load_listed()
    index = stock_index(listed)
    for stock in search_stocks(index, "トヨタ"):
        print(stock.code, stock.name, stock.market)
"""

import unicodedata
from dataclasses import dataclass

import pandas as pd

from core_gather import EXCLUDED_MARKETS, latest_listed_stocks_csv

MAX_CANDIDATES = 50  # 検索で返す銘柄の最大の数

# ひらがな -> カタカナ(銘柄名はカタカナが多いので、ひらがなで入力しても見つかるようにする)
HIRAGANA_TO_KATAKANA = {c: c + 0x60 for c in range(0x3041, 0x3097)}


def load_listed() -> pd.DataFrame:
    """最新の銘柄一覧を読み込む(コードが行の名前。core_gather.py が取得しない市場は除く)。"""
    listed = pd.read_csv(latest_listed_stocks_csv(), dtype=str, encoding="utf-8-sig")
    listed = listed[~listed["市場・商品区分"].isin(EXCLUDED_MARKETS)].dropna(subset=["コード"])
    return listed.set_index("コード")


def load_stock_names() -> dict[str, str]:
    """最新の銘柄一覧から、コード -> 銘柄名 の辞書を作る。銘柄一覧がなければ空の辞書。"""
    try:
        listed = pd.read_csv(latest_listed_stocks_csv(), dtype=str, encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    return dict(zip(listed["コード"], listed["銘柄名"]))


def normalize(text: str) -> str:
    """検索用に文字をそろえる(全角・半角、大文字・小文字、ひらがな・カタカナを区別しない)。"""
    return unicodedata.normalize("NFKC", text).casefold().translate(HIRAGANA_TO_KATAKANA)


@dataclass(frozen=True)
class Stock:
    """検索の対象になる銘柄 1 つ。"""
    code: str
    name: str
    market: str
    code_key: str  # 検索用にそろえたコード
    name_key: str  # 検索用にそろえた銘柄名


def stock_index(listed: pd.DataFrame) -> list[Stock]:
    """銘柄一覧(コードが行の名前。load_listed() の戻り値)から、検索用の一覧を作る。"""
    names = listed["銘柄名"].fillna("")
    markets = listed["市場・商品区分"].fillna("")
    return [
        Stock(code, name, market.split("（")[0], normalize(code), normalize(name))
        for code, name, market in zip(listed.index, names, markets)
    ]


def search_stocks(index: list[Stock], query: str, limit: int = MAX_CANDIDATES) -> list[Stock]:
    """銘柄コードまたは銘柄名に query を含む銘柄を返す。

    query を空白で区切り、すべての語をコードか銘柄名に含む銘柄を選ぶ。
    コードが先頭から一致する銘柄、銘柄名が先頭から一致する銘柄、それ以外の順に並べる。
    """
    words = normalize(query).split()
    if not words:
        return []
    first = words[0]

    def rank(stock: Stock) -> int:
        if stock.code_key.startswith(first):
            return 0
        return 1 if stock.name_key.startswith(first) else 2

    found = [s for s in index if all(w in s.code_key or w in s.name_key for w in words)]
    return sorted(found, key=rank)[:limit]  # 同じ順位の中は、銘柄一覧の順(コード順)のまま
