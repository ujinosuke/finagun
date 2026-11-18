"""ウォッチリストの保存ファイル(watchlist.json)の読み書き(GUI を含まない部分)。

ウォッチリストの画面は fina_watchlist.py。他のプログラム(fina_asset.py・fina_screening.py)からも
銘柄を足せるように、読み書きをここにまとめる。

使い方(他のプログラムから):
    added = add_codes("ウォッチリスト", ["7203", "6758"])
    codes = watched_codes()
"""

import json
import os

from core_gather import BASE_DIR

WATCHLIST_PATH = BASE_DIR / "watchlist.json"
FORMAT_VERSION = 1
DEFAULT_GROUP = "ウォッチリスト"  # グループが 1 つもないときに作るグループの名前


def read_watchlist() -> dict:
    """watchlist.json を読み込む。ファイルがなければ空の辞書を返す。"""
    if not WATCHLIST_PATH.exists():
        return {}
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("ファイルの形式が違います")
    return data


def write_watchlist(data: dict) -> None:
    """watchlist.json に保存する。

    書き込みの途中で止まっても元のファイルが壊れないよう、別のファイルに書いてから置き換える。
    """
    temporary = WATCHLIST_PATH.with_name(WATCHLIST_PATH.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temporary, WATCHLIST_PATH)


def watchlist_mtime() -> float | None:
    """watchlist.json の更新時刻。ファイルがなければ None。"""
    try:
        return WATCHLIST_PATH.stat().st_mtime
    except OSError:
        return None


def parse_groups(saved) -> list[dict]:
    """保存されていたグループを、{"name": 名前, "codes": [コード, ...]} の一覧に直す。

    手で書き換えたファイルでも止まらないよう、形の違うものや重複は除く。
    グループが 1 つもなければ、空のグループを 1 つ作る。
    """
    groups = []
    for group in saved if isinstance(saved, list) else []:
        if not isinstance(group, dict):
            continue
        name, codes = group.get("name"), group.get("codes")
        if not isinstance(name, str) or not name.strip() or any(g["name"] == name.strip() for g in groups):
            continue
        codes = [c for c in codes if isinstance(c, str) and c] if isinstance(codes, list) else []
        groups.append({"name": name.strip(), "codes": list(dict.fromkeys(codes))})
    return groups or [{"name": DEFAULT_GROUP, "codes": []}]


def group_names() -> list[str]:
    """グループの名前の一覧(ファイルが読めなければ、既定のグループだけ)。"""
    try:
        data = read_watchlist()
    except (OSError, ValueError):
        return [DEFAULT_GROUP]
    return [g["name"] for g in parse_groups(data.get("groups"))]


def add_codes(group_name: str, codes: list[str]) -> int:
    """指定のグループ(なければ作る)に銘柄を足して保存する。足した数を返す(登録済みの銘柄は数えない)。

    ファイルが壊れているときは ValueError、読み書きできないときは OSError。
    fina_watchlist.py を開いている間に足しても、fina_watchlist.py が保存するときに取り込まれる。
    """
    data = read_watchlist()
    groups = parse_groups(data.get("groups"))
    group = next((g for g in groups if g["name"] == group_name), None)
    if group is None:
        group = {"name": group_name, "codes": []}
        groups.append(group)
    added = [c for c in dict.fromkeys(codes) if c not in group["codes"]]
    group["codes"].extend(added)
    data["version"] = FORMAT_VERSION
    data["groups"] = groups
    write_watchlist(data)
    return len(added)


def watched_codes() -> list[str]:
    """全グループの銘柄(重複なし、登録順)。ファイルが読めなければ空。"""
    try:
        data = read_watchlist()
    except (OSError, ValueError):
        return []
    return list(dict.fromkeys(c for g in parse_groups(data.get("groups")) for c in g["codes"]))
