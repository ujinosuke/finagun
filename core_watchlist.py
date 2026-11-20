"""ウォッチリストの保存ファイル(watchlist.json)の読み書き(GUI を含まない部分)。

ウォッチリストの画面は fina_watchlist.py。グループは銘柄比較(fina_compare.py)と共有する。
他のプログラム(fina_asset.py・fina_screening.py)からも銘柄を足せるように、読み書きをここにまとめる。

複数のプログラムを同時に開いていても変更を消さないよう、グループや銘柄を変えるときは
update_groups()(とそれを使う関数)で、毎回ファイルを読み直してから変えて保存する。

使い方(他のプログラムから):
    added = add_codes("ウォッチリスト", ["7203", "6758"])
    codes = watched_codes()
    groups = rename_group("半導体", "半導体・製造装置")
"""

import json
import os

from core_gather import BASE_DIR

WATCHLIST_PATH = BASE_DIR / "watchlist.json"
FORMAT_VERSION = 1
DEFAULT_GROUP = "ウォッチリスト"  # グループが 1 つもないときに作るグループの名前
RESERVED_NAMES = ("保有資産",)    # 銘柄比較の固定のグループの名前(ここでは作れない)


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


def load_groups() -> list[dict]:
    """グループの一覧(ファイルがなければ、空の既定のグループだけ)。

    ファイルが壊れているときは ValueError、読めないときは OSError。
    """
    return parse_groups(read_watchlist().get("groups"))


def update_groups(change) -> list[dict]:
    """ファイルを読み直し、change(groups) でグループの一覧をその場で書き換えて保存する。変えたあとの一覧を返す。

    他のプログラムでの変更を消さないよう、画面が持っている一覧ではなく、ファイルの一覧を変える。
    グループが 1 つもなくなったら、空の既定のグループを作る。
    ファイルが壊れているときは ValueError、読み書きできないときは OSError。
    """
    data = read_watchlist()
    groups = parse_groups(data.get("groups"))
    change(groups)
    if not groups:
        groups.append({"name": DEFAULT_GROUP, "codes": []})
    data["version"] = FORMAT_VERSION
    data["groups"] = groups
    write_watchlist(data)
    return groups


def name_error(name: str, groups: list[dict]) -> str | None:
    """グループの名前に使えないときは、その理由。使えるときは None。"""
    if not name:
        return "グループの名前を入れてください。"
    if name in RESERVED_NAMES:
        return f"「{name}」は銘柄比較の固定のグループの名前なので、使えません。"
    if any(g["name"] == name for g in groups):
        return f"「{name}」という名前のグループは、すでにあります。"
    return None


def _find(groups: list[dict], name: str) -> dict | None:
    return next((g for g in groups if g["name"] == name), None)


def create_group(name: str) -> list[dict]:
    """グループを作る(同じ名前があれば何もしない)。"""
    def change(groups):
        if _find(groups, name) is None:
            groups.append({"name": name, "codes": []})
    return update_groups(change)


def rename_group(old: str, new: str) -> list[dict]:
    """グループの名前を変える。新しい名前がすでにあれば ValueError(元のグループがなければ何もしない)。"""
    def change(groups):
        group = _find(groups, old)
        if group is None:
            return
        error = name_error(new, [g for g in groups if g is not group])
        if error:
            raise ValueError(error)
        group["name"] = new
    return update_groups(change)


def delete_group(name: str) -> list[dict]:
    """グループを消す(最後の 1 つを消したときは、空の既定のグループを作り直す)。"""
    return update_groups(lambda groups: groups.remove(g) if (g := _find(groups, name)) else None)


def add_codes(group_name: str, codes: list[str]) -> int:
    """指定のグループ(なければ作る)に銘柄を足して保存する。足した数を返す(登録済みの銘柄は数えない)。

    ファイルが壊れているときは ValueError、読み書きできないときは OSError。
    """
    added = []

    def change(groups):
        group = _find(groups, group_name)
        if group is None:
            group = {"name": group_name, "codes": []}
            groups.append(group)
        added.extend(c for c in dict.fromkeys(codes) if c not in group["codes"])
        group["codes"].extend(added)
    update_groups(change)
    return len(added)


def remove_codes(group_name: str, codes) -> list[dict]:
    """グループから銘柄を外す。"""
    removed = set(codes)

    def change(groups):
        group = _find(groups, group_name)
        if group is not None:
            group["codes"][:] = [c for c in group["codes"] if c not in removed]
    return update_groups(change)


def move_code(group_name: str, code: str, step: int) -> list[dict]:
    """グループの中で、銘柄を上(step=-1)または下(step=1)に動かす(端では動かさない)。"""
    return move_codes(group_name, [code], step)


def move_codes(group_name: str, codes, step: int) -> list[dict]:
    """グループの中で、いくつかの銘柄をまとめて 1 つ上(step=-1)または下(step=1)に動かす。

    端に着いた銘柄は動かさない。その銘柄に続いて並んでいる選んだ銘柄も、追い越さずに止まる。
    """
    moving = set(codes)

    def change(groups):
        group = _find(groups, group_name)
        if group is None:
            return
        order = group["codes"]
        # 動かす向きの先頭から順に、1 つ隣の「動かさない銘柄」と入れ替える
        indexes = range(len(order)) if step < 0 else range(len(order) - 1, -1, -1)
        for i in indexes:
            j = i + step
            if order[i] in moving and 0 <= j < len(order) and order[j] not in moving:
                order[i], order[j] = order[j], order[i]
    return update_groups(change)


def merge_groups(incoming: list[dict]) -> list[dict]:
    """グループの一覧を取り込む(前の版の compare.json のグループを移すときに使う)。

    同じ名前のグループがあれば、まだない銘柄だけを足す。使えない名前(固定のグループの名前)は除く。
    """
    def change(groups):
        for new in incoming:
            if new["name"] in RESERVED_NAMES:
                continue
            group = _find(groups, new["name"])
            if group is None:
                groups.append({"name": new["name"], "codes": list(new["codes"])})
            else:
                group["codes"].extend(c for c in new["codes"] if c not in group["codes"])
    return update_groups(change)


def write_selected(group_name: str, code: str | None) -> None:
    """ウォッチリストで選んでいるグループと銘柄を保存する(グループはファイルのものをそのまま残す)。"""
    data = read_watchlist()
    data["version"] = FORMAT_VERSION
    data["groups"] = parse_groups(data.get("groups"))
    data["selected"] = {"group": group_name, "code": code}
    data.pop("chart", None)  # 前の版の図形などは、起動時に charts/ へ引き継ぎ済み
    write_watchlist(data)


def watched_codes() -> list[str]:
    """全グループの銘柄(重複なし、登録順)。ファイルが読めなければ空。"""
    try:
        data = read_watchlist()
    except (OSError, ValueError):
        return []
    return list(dict.fromkeys(c for g in parse_groups(data.get("groups")) for c in g["codes"]))
