"""条件セット(シグナル層 + フィルター層)を編集する画面部品。

fina_screening.py の条件パネルで使う(将来は fina_bktest.py のストラテジーの編集でも使う想定)。
設計は pico_strategy-screening-design.md の 4 章(条件の合成)と 7 章(UI)。

    ConditionSetEditor  シグナルの行とフィルターの行を並べて編集する部品。
                        各行は「文として読める表示」と［編集］［削除］(フィルターは「除外」のチェックも)
    ask_condition_row   条件 1 行を選ぶ・直す小さなダイアログ。
                        パラメータの入力欄は、コンディションの Param の宣言から作る
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from core_condition import CONDITION_TYPES
from core_strategies import ConditionRow
from gui_help import attach as attach_help
from gui_theme import PAD


def ask_condition_row(parent, title: str, row: ConditionRow | None = None,
                      allow_exclude: bool = False) -> ConditionRow | None:
    """条件 1 行を入力してもらう。row を渡すとその内容で始める。取り消しなら None。"""
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent.winfo_toplevel())
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill="both", expand=True)

    kind_var = tk.StringVar(value=row.condition if row else next(iter(CONDITION_TYPES)))
    state_var = tk.StringVar()
    exclude_var = tk.BooleanVar(value=row.exclude if row else False)
    param_vars: dict[str, tk.StringVar] = {}
    state_names: dict[str, str] = {}  # 状態の表示名 -> 状態の名前

    ttk.Label(frame, text="コンディション").grid(row=0, column=0, sticky="w", pady=(0, 4))
    kind_box = ttk.Combobox(frame, textvariable=kind_var, values=list(CONDITION_TYPES), state="readonly", width=22)
    kind_box.grid(row=0, column=1, columnspan=2, sticky="w", pady=(0, 4))
    attach_help(kind_box, lambda: CONDITION_TYPES[kind_var.get()].term)
    ttk.Label(frame, text="状態").grid(row=1, column=0, sticky="w", pady=(0, 4))
    state_box = ttk.Combobox(frame, textvariable=state_var, state="readonly", width=22)
    state_box.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 4))
    params_frame = ttk.Frame(frame)
    params_frame.grid(row=2, column=0, columnspan=3, sticky="w", pady=(PAD, 0))
    if allow_exclude:
        check = ttk.Checkbutton(frame, text="除外にする(成り立ったら見送る)", variable=exclude_var)
        check.grid(row=3, column=0, columnspan=3, sticky="w", pady=(PAD, 0))
        attach_help(check, "exclude")

    def show_kind(start: ConditionRow | None = None):
        """選んだ種類の状態とパラメータの欄を作る。start があれば、その値を入れる。"""
        cls = CONDITION_TYPES[kind_var.get()]
        sample = cls()  # 状態の一覧を知るため、既定値で作る
        state_names.clear()
        state_names.update({style.label: name for name, style in sample.states.items()})
        state_box.configure(values=list(state_names))
        state = start.state if start else next(iter(sample.states))
        state_var.set(sample.states[state].label)
        for child in params_frame.winfo_children():
            child.destroy()
        param_vars.clear()
        if not cls.params:
            ttk.Label(params_frame, text="パラメータはありません", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        for n, spec in enumerate(cls.params):
            value = start.params[spec.name] if start else spec.default
            param_vars[spec.name] = tk.StringVar(value=f"{value:g}" if isinstance(value, float) else str(value))
            ttk.Label(params_frame, text=spec.label).grid(row=n, column=0, sticky="w", padx=(0, PAD), pady=2)
            ttk.Entry(params_frame, textvariable=param_vars[spec.name], width=10).grid(row=n, column=1, sticky="w")
            ttk.Label(params_frame, text=f"{spec.low:g} 〜 {spec.high:g}", style="Muted.TLabel").grid(
                row=n, column=2, sticky="w", padx=(PAD, 0))

    kind_box.bind("<<ComboboxSelected>>", lambda e: show_kind())
    show_kind(row)
    result: list[ConditionRow] = []

    def accept(_event=None):
        cls = CONDITION_TYPES[kind_var.get()]
        params = {}
        for spec in cls.params:
            text = param_vars[spec.name].get().strip()
            try:
                params[spec.name] = int(text) if isinstance(spec.default, int) else float(text)
            except ValueError:
                kind = "整数" if isinstance(spec.default, int) else "数"
                messagebox.showerror(title, f"{spec.label} は{kind}で入力してください: {text}", parent=window)
                return
        try:
            result.append(ConditionRow(kind_var.get(), state_names[state_var.get()], params, exclude_var.get()))
        except ValueError as e:
            messagebox.showerror(title, str(e), parent=window)
            return
        window.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=4, column=0, columnspan=3, sticky="e", pady=(12, 0))
    ttk.Button(buttons, text="OK", style="Accent.TButton", command=accept).pack(side="left")
    ttk.Button(buttons, text="取消", command=window.destroy).pack(side="left", padx=(4, 0))
    window.bind("<Return>", accept)
    window.bind("<Escape>", lambda e: window.destroy())
    window.grab_set()
    window.wait_window()
    return result[0] if result else None


class ConditionSetEditor(ttk.Frame):
    """シグナルの行とフィルターの行を編集する部品。on_change は行が変わるたびに呼ばれる。"""

    def __init__(self, master, on_change: Callable[[], None] | None = None, wraplength: int = 300):
        super().__init__(master)
        self.on_change = on_change
        self.wraplength = wraplength
        self.signals: list[ConditionRow] = []
        self.filters: list[ConditionRow] = []
        self.enabled = True
        self.buttons: list[ttk.Widget] = []  # 実行中に押せなくする部品

        # 枠の見出しにマウスを乗せると、用語の説明を出す(枠全体に付けると、中のボタンの上でも出てしまう)
        label = ttk.Label(self, text="シグナル(どれか 1 つが点灯したら候補)")
        attach_help(label, "signal")
        self.signal_box = ttk.LabelFrame(self, labelwidget=label, padding=(PAD, 4))
        self.signal_box.pack(side="top", fill="x")
        self.signal_rows = ttk.Frame(self.signal_box)
        self.signal_rows.pack(side="top", fill="x")
        add = ttk.Button(self.signal_box, text="＋ シグナルを追加", command=lambda: self._add(False))
        add.pack(side="top", anchor="w", pady=(4, 0))
        self.buttons.append(add)

        label = ttk.Label(self, text="フィルター(すべて満たす。除外は成り立ったら見送る)")
        attach_help(label, "filter")
        self.filter_box = ttk.LabelFrame(self, labelwidget=label, padding=(PAD, 4))
        self.filter_box.pack(side="top", fill="x", pady=(PAD, 0))
        self.filter_rows = ttk.Frame(self.filter_box)
        self.filter_rows.pack(side="top", fill="x")
        add = ttk.Button(self.filter_box, text="＋ フィルターを追加", command=lambda: self._add(True))
        add.pack(side="top", anchor="w", pady=(4, 0))
        self.buttons.append(add)
        self._render()

    def set_rows(self, signals, filters) -> None:
        self.signals, self.filters = list(signals), list(filters)
        self._render()

    def set_enabled(self, enabled: bool) -> None:
        """実行中は編集できないようにする。"""
        self.enabled = enabled
        self._render()

    def _add(self, is_filter: bool):
        row = ask_condition_row(self, "フィルターを追加" if is_filter else "シグナルを追加", allow_exclude=is_filter)
        if row is not None:
            (self.filters if is_filter else self.signals).append(row)
            self._changed()

    def _edit(self, is_filter: bool, n: int):
        rows = self.filters if is_filter else self.signals
        row = ask_condition_row(self, "条件を編集", rows[n], allow_exclude=is_filter)
        if row is not None:
            rows[n] = row
            self._changed()

    def _delete(self, is_filter: bool, n: int):
        del (self.filters if is_filter else self.signals)[n]
        self._changed()

    def _toggle_exclude(self, n: int, var: tk.BooleanVar):
        row = self.filters[n]
        self.filters[n] = ConditionRow(row.condition, row.state, row.params, var.get())
        self._changed()

    def _changed(self):
        self._render()
        if self.on_change:
            self.on_change()

    def _render(self):
        """行を作り直す。"""
        state = "normal" if self.enabled else "disabled"
        for button in self.buttons:
            button.configure(state=state)
        for parent, rows, is_filter in ((self.signal_rows, self.signals, False), (self.filter_rows, self.filters, True)):
            for child in parent.winfo_children():
                child.destroy()
            if not rows:
                ttk.Label(parent, text="なし", style="Muted.TLabel").pack(side="top", anchor="w")
            for n, row in enumerate(rows):
                line = ttk.Frame(parent)
                line.pack(side="top", fill="x", pady=1)
                ttk.Button(line, text="削除", width=5, state=state,
                           command=lambda f=is_filter, i=n: self._delete(f, i)).pack(side="right")
                ttk.Button(line, text="編集", width=5, state=state,
                           command=lambda f=is_filter, i=n: self._edit(f, i)).pack(side="right", padx=(4, 2))
                if is_filter:
                    var = tk.BooleanVar(value=row.exclude)
                    check = ttk.Checkbutton(line, text="除外", variable=var, state=state,
                                            command=lambda i=n, v=var: self._toggle_exclude(i, v))
                    check.pack(side="right")
                    attach_help(check, "exclude")
                text = ("除外: " if row.exclude else "") + row.label
                label = ttk.Label(line, text=text, wraplength=self.wraplength, justify="left",
                                  style="Warning.TLabel" if row.exclude else "TLabel")
                label.pack(side="left", anchor="w")
                attach_help(label, CONDITION_TYPES[row.condition].term)  # その行のコンディションの説明
