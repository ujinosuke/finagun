"""ToreCan のバージョンと、全プログラム共通の設定(settings.json)。GUI を含まない部分。

設計は ToreCan_design.md、仕様は torecan-home-spec.md を参照。

バージョン
    __version__ をこの 1 か所だけで持つ。ホーム画面と、全プログラムのタイトルバー(app_title)で使う。
    上げ方: 変更のたびに 0.1.xxx の xxx(3 桁)を 1 つ上げる(0.1.003 → 0.1.004)。メジャー・マイナーは本人が決めたときだけ上げる。README.md の更新履歴に 1 行足す。
全体設定(settings.json)
    色の決まり(日本式・海外式)、銘柄比較のベンチマーク・相関の期間、データフォルダの場所。
    各プログラムは起動時に 1 回読む(開いている間にホームで変えても、起動し直すまで反映されない)。
ロックファイル
    ToreCan.py の二重起動の防止(torecan.lock)と、データ更新の同時実行の防止(gather.lock)に使う。

このモジュールは他のモジュールを import しない(core_gather などから読まれるので、循環を避ける)。

使い方(他のプログラムから):
    root.title(app_title("asset"))      # "ToreCan asset v0.1.003"
    up, down = market_colors()          # 上昇・下落(利益・損失)の色
    settings = load_settings()
    update_settings(benchmark="1321")   # 一部だけ変えて保存する
"""

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

__version__ = "0.1.007"

APP_DIR = Path(__file__).resolve().parent  # プログラムのフォルダ(保存ファイルの置き場所)
SETTINGS_PATH = APP_DIR / "settings.json"
FORMAT_VERSION = 1

# 色の決まり -> 表示名
COLOR_SCHEMES = {"western": "海外式(上昇・利益が緑)", "japan": "日本式(上昇・利益が赤)"}
GREEN = "#26a69a"  # gui_chart の陽線(海外式)の色
RED = "#ef5350"    # gui_chart の陰線(海外式)の色
# 文字に使う濃いめの色(白い背景の上で読みやすい)
TEXT_GREEN = "#2e7d32"
TEXT_RED = "#d32f2f"

# 選べる値(fina_compare と同じ。ここで持つのは、このモジュールが他を import しないため)
BENCHMARK_CODES = ("1306", "1321", "1591", "2516", "1343", "1577", "")  # "" は「なし」
CORRELATION_PERIOD_NAMES = ("3か月", "6か月", "1年", "全期間")


def app_title(name: str) -> str:
    """タイトルバーの文字。app_title("asset") -> "ToreCan asset v0.1.003"。"""
    return f"ToreCan {name} v{__version__}"


@dataclass(frozen=True)
class Settings:
    """全体設定。"""
    color_scheme: str = "western"   # "western"(上昇・利益が緑) / "japan"(上昇・利益が赤)
    benchmark: str = "1306"         # 銘柄比較の相関・ベータのベンチマーク("" は「なし」)
    corr_period: str = "1年"        # 銘柄比較の相関の期間
    data_dir: str = ""              # listed_stocks・stock_database を入れたフォルダ。空ならプログラムのフォルダ
    # チャートの既定の表示(足・ボリンジャーバンド・出来高・オシレーター・コンディション・十字線)。
    # 銘柄ごとのファイル(charts/)がない銘柄はこの表示で開く。値を確かめるのは gui_chart.chart_defaults
    chart_defaults: dict = field(default_factory=dict)
    draw_color: str = ""            # チャートで図形を描く色(全銘柄で 1 つ)。空なら gui_chart の最初の色
    help_tooltips: bool = True      # 投資用語にマウスを乗せたとき、説明の吹き出しを出すか(gui_help.py)


CHART_DEFAULT_KEYS = {  # chart_defaults の項目 -> 値の型
    "timeframe": str, "bollinger": bool, "volume": bool, "oscillator_on": bool, "oscillator": str,
    "condition_on": bool, "condition": str, "crosshair": bool,
}

CHOICES = {
    "color_scheme": tuple(COLOR_SCHEMES),
    "benchmark": BENCHMARK_CODES,
    "corr_period": CORRELATION_PERIOD_NAMES,
}


def parse_settings(data) -> Settings:
    """保存されていた設定を Settings に直す。おかしな値は既定値にする。"""
    values = {}
    if isinstance(data, dict):
        for f in fields(Settings):
            value = data.get(f.name)
            if f.name in CHOICES:
                if value in CHOICES[f.name]:
                    values[f.name] = value
            elif f.name == "chart_defaults":
                if isinstance(value, dict):
                    values[f.name] = {k: v for k, v in value.items()
                                      if k in CHART_DEFAULT_KEYS and type(v) is CHART_DEFAULT_KEYS[k]}
            elif isinstance(f.default, bool):
                if isinstance(value, bool):
                    values[f.name] = value
            elif isinstance(value, str):
                values[f.name] = value.strip()
    return Settings(**values)


def load_settings(path: Path | None = None) -> Settings:
    """settings.json を読む。ないときは既定値。壊れているときは .bak に残して既定値。"""
    path = path or SETTINGS_PATH
    if not path.exists():
        return Settings()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError:
        try:
            os.replace(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass
        return Settings()
    except OSError:
        return Settings()
    return parse_settings(data)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """settings.json に保存する。途中で止まっても元のファイルが壊れないよう、別のファイルから置き換える。"""
    path = path or SETTINGS_PATH
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump({"version": FORMAT_VERSION, **asdict(settings)}, f, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def update_settings(path: Path | None = None, **changes) -> Settings:
    """ファイルを読み直してから一部だけ変えて保存する(他のプログラムが変えた項目を消さないため)。"""
    settings = replace(load_settings(path), **changes)
    save_settings(settings, path)
    return settings


def settings_exist(path: Path | None = None) -> bool:
    return (path or SETTINGS_PATH).exists()


# 起動したときの設定(データフォルダ・色は、起動時に 1 回だけ決める)
_SETTINGS = load_settings()


def current_settings() -> Settings:
    """起動したときに読んだ設定。"""
    return _SETTINGS


def data_root(settings: Settings | None = None) -> Path:
    """listed_stocks・stock_database を入れたフォルダ。設定が空ならプログラムのフォルダ。"""
    directory = (settings or _SETTINGS).data_dir
    return Path(directory).expanduser() if directory else APP_DIR


def is_japan_style(settings: Settings | None = None) -> bool:
    return (settings or _SETTINGS).color_scheme == "japan"


def market_colors(settings: Settings | None = None) -> tuple[str, str]:
    """チャートの (上昇の色, 下落の色)。海外式は 緑・赤、日本式は 赤・緑。"""
    return (RED, GREEN) if is_japan_style(settings) else (GREEN, RED)


def profit_colors(settings: Settings | None = None) -> tuple[str, str]:
    """± の数値の文字の (利益・上昇の色, 損失・下落の色)。白い背景で読みやすい濃さ。"""
    return (TEXT_RED, TEXT_GREEN) if is_japan_style(settings) else (TEXT_GREEN, TEXT_RED)


# ---- ロックファイル(二重起動・同時実行の防止) ----

def process_alive(pid: int) -> bool:
    """番号 pid のプロセスが動いているか。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_owner(path: Path) -> int | None:
    """ロックファイルを持っている、動いているプロセスの番号。なければ(落ちて残った lock も)None。"""
    try:
        pid = int(path.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return None
    return pid if process_alive(pid) and pid != os.getpid() else None


def acquire_lock(path: Path) -> bool:
    """ロックファイルを作る。他の動いているプロセスが持っていれば False。

    落ちて残ったロックファイル(番号のプロセスがもう動いていない)は、作り直す。
    """
    for _ in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if lock_owner(path) is not None:
                return False
            try:
                path.unlink()  # 落ちて残ったもの
            except OSError:
                return False
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    return False


def release_lock(path: Path) -> None:
    """自分が持っているロックファイルを消す。"""
    try:
        if int(path.read_text(encoding="utf-8").strip() or 0) == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "settings.json"
        assert load_settings(path) == Settings()
        save_settings(Settings(color_scheme="japan"), path)
        assert update_settings(path, benchmark="1321") == Settings(color_scheme="japan", benchmark="1321")
        path.write_text('{"color_scheme": "purple", "benchmark": 1306, "data_dir": " D:/data "}', encoding="utf-8")
        assert load_settings(path) == Settings(data_dir="D:/data")
        path.write_text("{壊れた", encoding="utf-8")
        assert load_settings(path) == Settings() and path.with_name("settings.json.bak").exists()
        assert market_colors(Settings()) == (GREEN, RED) and market_colors(Settings("japan")) == (RED, GREEN)

        lock = Path(directory) / "test.lock"
        assert acquire_lock(lock) and lock.read_text() == str(os.getpid())
        assert lock_owner(lock) is None  # 自分が持っている
        release_lock(lock)
        assert not lock.exists()
        lock.write_text("999999")  # 落ちて残ったもの
        assert acquire_lock(lock)
        release_lock(lock)
        lock.write_text(str(os.getppid()))  # 動いている他のプロセス
        assert not acquire_lock(lock)
    print(f"core_settings: すべての確認に通りました(ToreCan v{__version__})。")


if __name__ == "__main__":
    _self_test()
