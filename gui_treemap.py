"""ツリーマップ(ヒートマップ)の描画部品。四角の大きさで量を、色で値の上がり下がりを示す。

Finviz のマップのように、グループ(セクターなど)ごとに四角をまとめ、その中に銘柄の四角を並べる。
並べ方は squarified treemap(四角がなるべく正方形に近くなるように並べる方法)。外部ライブラリは使わない。

色は 0 を灰色にし、上昇と下落で別の色を濃くしていく(7 段)。
日本式(赤 = 上昇、緑 = 下落)が既定で、up_is_red=False で逆にできる。

使い方(他のプログラムから):
    canvas = TreemapCanvas(parent, on_open=lambda key: print(key))
    canvas.pack(fill="both", expand=True)
    canvas.set_items([TreemapItem("7203", "7203", 500000, 1.2, group="輸送用機器")], scale=3.0)
"""

import math
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass

# 色(7 段: 下落 3 段・変化なし・上昇 3 段)
RED_STEPS = ("#f2a7a6", "#e34948", "#b3201f")
GREEN_STEPS = ("#a6d8a6", "#2e9e3c", "#006300")
NEUTRAL = "#d9d8d4"
NO_VALUE = "#efeeeb"     # 値のない四角
BACKGROUND = "#ffffff"   # 四角のすき間の色
GROUP_FILL = "#f4f3f0"   # グループの見出しの帯
TEXT_DARK = "#1b1b1a"
TEXT_LIGHT = "#ffffff"
TEXT_MUTED = "#52514e"
GAP = 2                  # 四角どうしのすき間(ピクセル)
HEADER = 18              # グループの見出しの高さ(ピクセル)
NEUTRAL_RANGE = 0.1      # scale に対してこの割合より小さい値は、変化なし(灰色)にする


@dataclass(frozen=True)
class TreemapItem:
    """ツリーマップの四角 1 つ。"""
    key: str              # ダブルクリックしたときに on_open に渡す値
    label: str            # 四角の 1 行目(コードなど)
    size: float           # 面積(評価額など)。0 以下は描かない
    value: float | None   # 色を決める値(% など)。None は値なし
    group: str = ""       # グループ名(全部空ならグループ分けしない)
    name: str = ""        # 大きな四角に出す 2 行目(銘柄名など)
    detail: str = ""      # マウスを乗せたときに出す説明


def squarify(sizes: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """sizes(大きい順に並べておく)を、長方形 (x, y, w, h) の中に並べる。各四角の (x, y, w, h) を同じ順で返す。"""
    total = sum(sizes)
    if total <= 0 or w <= 0 or h <= 0:
        return [(x, y, 0.0, 0.0) for _ in sizes]
    areas = [s * w * h / total for s in sizes]
    rects: list[tuple[float, float, float, float]] = []
    i = 0
    while i < len(areas):
        short = min(w, h)
        row = [areas[i]]
        j = i + 1
        while j < len(areas) and _worst(row + [areas[j]], short) <= _worst(row, short):
            row.append(areas[j])
            j += 1
        # 短い辺に沿って 1 列並べ、残りの長方形で続ける
        row_sum = sum(row)
        thickness = row_sum / short
        offset = 0.0
        for area in row:
            length = area / thickness
            if w >= h:  # 横長: 左の端に縦の列を置く
                rects.append((x, y + offset, thickness, length))
            else:       # 縦長: 上の端に横の列を置く
                rects.append((x + offset, y, length, thickness))
            offset += length
        if w >= h:
            x, w = x + thickness, w - thickness
        else:
            y, h = y + thickness, h - thickness
        i = j
    return rects


def _worst(row: list[float], short: float) -> float:
    """列の中で一番細長い四角の縦横比(小さいほどよい)。"""
    s = sum(row)
    return max(max(short * short * a / (s * s), (s * s) / (short * short * a)) for a in row)


def fill_color(value: float | None, scale: float, up_is_red: bool = True) -> str:
    """値の色。scale 以上で一番濃くなる。"""
    if value is None or not math.isfinite(value):
        return NO_VALUE
    t = value / scale if scale > 0 else 0.0
    if abs(t) < NEUTRAL_RANGE:
        return NEUTRAL
    step = min(int(abs(t) * 3 - 1e-9), 2) if abs(t) < 1 else 2
    up, down = (RED_STEPS, GREEN_STEPS) if up_is_red else (GREEN_STEPS, RED_STEPS)
    return (up if t > 0 else down)[step]


def text_color(fill: str) -> str:
    """塗りの色の上で読みやすい文字の色(暗い塗りには白)。"""
    r, g, b = (int(fill[i:i + 2], 16) / 255 for i in (1, 3, 5))
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return TEXT_LIGHT if luminance < 0.45 else TEXT_DARK


class TreemapCanvas(tk.Canvas):
    """ツリーマップを描く Canvas。大きさが変わると描き直す。"""

    def __init__(self, master, on_open: Callable[[str], None] | None = None, value_format: str = "{:+.2f}%"):
        super().__init__(master, background=BACKGROUND, highlightthickness=0)
        self.on_open = on_open
        self.value_format = value_format
        self.items: list[TreemapItem] = []
        self.scale = 1.0
        self.up_is_red = True
        self.hit: list[tuple[tuple[float, float, float, float], TreemapItem]] = []  # 四角の位置と項目
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self.delete("tooltip"))
        self.bind("<Double-Button-1>", self._on_double_click)

    def set_items(self, items: list[TreemapItem], scale: float, up_is_red: bool = True) -> None:
        """描く項目を入れ替える。scale は色が一番濃くなる値。"""
        self.items = [i for i in items if i.size > 0]
        self.scale = scale
        self.up_is_red = up_is_red
        self._draw()

    # ---- 描画 ----

    def _draw(self):
        self.delete("all")
        self.hit = []
        width, height = self.winfo_width(), self.winfo_height()
        if width < 10 or height < 10:
            return
        if not self.items:
            self.create_text(width / 2, height / 2, text="表示する保有がありません", fill=TEXT_MUTED)
            return
        groups: dict[str, list[TreemapItem]] = {}
        for item in self.items:
            groups.setdefault(item.group, []).append(item)
        grouped = len(groups) > 1 or next(iter(groups)) != ""
        ordered = sorted(groups.items(), key=lambda g: -sum(i.size for i in g[1]))
        group_rects = squarify([sum(i.size for i in g) for _, g in ordered], 0, 0, width, height)
        for (name, members), (gx, gy, gw, gh) in zip(ordered, group_rects):
            members = sorted(members, key=lambda i: -i.size)
            if grouped and gh > HEADER + 10 and gw > 30:
                self.create_rectangle(gx, gy, gx + gw, gy + HEADER, fill=GROUP_FILL, outline=BACKGROUND, width=GAP)
                self.create_text(gx + 4, gy + HEADER / 2, text=self._fit(name, gw - 8, 9), anchor="w",
                                 fill=TEXT_MUTED, font=("", 9, "bold"))
                gy, gh = gy + HEADER, gh - HEADER
            for item, rect in zip(members, squarify([i.size for i in members], gx, gy, gw, gh)):
                self._draw_item(item, rect)

    def _draw_item(self, item: TreemapItem, rect: tuple[float, float, float, float]):
        x, y, w, h = rect
        fill = fill_color(item.value, self.scale, self.up_is_red)
        self.create_rectangle(x, y, x + w, y + h, fill=fill, outline=BACKGROUND, width=GAP)
        self.hit.append((rect, item))
        color = text_color(fill)
        value = "" if item.value is None else self.value_format.format(item.value)
        lines = []
        if w > 34 and h > 16:
            lines.append((item.label, ("", 10, "bold")))
        if w > 120 and h > 58 and item.name:
            lines.append((self._fit(item.name, w - 8, 9), ("", 9)))
        if w > 48 and h > 34 and value:
            lines.append((value, ("", 9)))
        if not lines:
            return
        line_height = 15
        top = y + h / 2 - line_height * (len(lines) - 1) / 2
        for n, (text, font) in enumerate(lines):
            self.create_text(x + w / 2, top + n * line_height, text=text, fill=color, font=font)

    @staticmethod
    def _fit(text: str, width: float, size: int) -> str:
        """幅に収まるよう文字を切る(1 文字の幅を、だいたい文字の大きさとみなす)。"""
        limit = max(int(width / (size + 3)), 1)
        return text if len(text) <= limit else text[: max(limit - 1, 1)] + "…"

    # ---- マウス ----

    def _item_at(self, x: float, y: float) -> TreemapItem | None:
        for (rx, ry, rw, rh), item in self.hit:
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return item
        return None

    def _on_motion(self, event):
        self.delete("tooltip")
        item = self._item_at(event.x, event.y)
        if item is None:
            return
        text = item.detail or item.label
        label = self.create_text(0, 0, text=text, anchor="nw", fill=TEXT_DARK, font=("", 9), tags="tooltip")
        x0, y0, x1, y1 = self.bbox(label)
        tw, th = x1 - x0 + 12, y1 - y0 + 8
        # マウスの右下に出す。はみ出すなら左・上に寄せる
        tx = event.x + 14 if event.x + 14 + tw < self.winfo_width() else event.x - tw - 6
        ty = event.y + 14 if event.y + 14 + th < self.winfo_height() else event.y - th - 6
        self.coords(label, tx + 6, ty + 4)
        background = self.create_rectangle(tx, ty, tx + tw, ty + th, fill="#ffffff", outline="#8a8984",
                                           tags="tooltip")
        self.tag_raise(label, background)

    def _on_double_click(self, event):
        item = self._item_at(event.x, event.y)
        if item is not None and self.on_open is not None:
            self.on_open(item.key)
