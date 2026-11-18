"""保有銘柄の配当情報を yfinance から取得し、JSON にキャッシュする(GUI を含まない部分)。

設計は fina_asset_design_phase2.md の §5 を参照。配当は参考表示だけで、収支としては記録しない。

- Ticker.dividends  過去の配当(権利落ち日と 1 株配当)
- Ticker.calendar   次回の権利落ち日("Ex-Dividend Date")・支払日("Dividend Date"。日本株は取れないことが多い)

Yahoo のレート制限を避けるため、全銘柄ではなく保有銘柄(+ ウォッチリストの銘柄)だけを、
キャッシュが古いものに限って、1 銘柄ずつ間をあけて取得する。

使い方(他のプログラムから):
    cache = DividendCache()
    cache.load()
    for code in cache.stale_codes(["7203", "9202"]):
        cache.fetch(code)
        time.sleep(FETCH_INTERVAL)
    cache.save()
    info = cache.get("7203")
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf

from core_gather import BASE_DIR

DIVIDEND_CACHE_PATH = BASE_DIR / "dividend_cache.json"
FORMAT_VERSION = 1
REFRESH_DAYS = 7      # この日数より古いキャッシュは取り直す
FETCH_INTERVAL = 1.0  # 1 銘柄ごとにあける秒数
MATCH_DAYS = 45       # 次回の権利落ち日を、去年の同じ時期の配当と同じ回とみなす日数の幅

# 取得できなかった銘柄について yfinance が出すエラー表示を抑える(結果はこのプログラムで表示する)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def next_weekday(day: date) -> date:
    """翌営業日(土日だけを除く。祝日は考えない)。"""
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def previous_weekday(day: date) -> date:
    """前営業日(土日だけを除く。祝日は考えない)。"""
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


@dataclass(frozen=True)
class ScheduledDividend:
    """今後の配当 1 回分。"""
    ex_date: str        # 権利落ち日
    amount: float       # 1 株配当
    confirmed: bool     # True なら yfinance の次回の予定、False なら去年の配当から推定したもの

    @property
    def record_date(self) -> str:
        """権利確定日の目安(権利落ち日の翌営業日)。"""
        return next_weekday(date.fromisoformat(self.ex_date)).isoformat()

    @property
    def last_cum_date(self) -> str:
        """権利付最終日の目安(権利落ち日の前営業日。この日の終わりまでに買えば配当がもらえる)。"""
        return previous_weekday(date.fromisoformat(self.ex_date)).isoformat()


@dataclass(frozen=True)
class DividendInfo:
    """1 銘柄の配当情報。"""
    code: str
    history: list[tuple[str, float]]  # (権利落ち日, 1 株配当)。古い順
    next_ex_date: str | None
    next_pay_date: str | None
    fetched_at: str                   # 取得した日時 "YYYY-MM-DDTHH:MM:SS"
    error: str = ""                   # 取得できなかった理由

    def trailing_annual(self, today: str) -> float:
        """過去 12 か月の 1 株配当の合計。"""
        start = (date.fromisoformat(today) - timedelta(days=365)).isoformat()
        return sum(amount for day, amount in self.history if start < day <= today)

    def last_amount(self) -> float | None:
        """直近の 1 株配当。"""
        return self.history[-1][1] if self.history else None

    def schedule(self, today: str) -> list[ScheduledDividend]:
        """今後 12 か月の配当の予定。

        去年(過去 12 か月)の配当を 1 年後ろにずらして推定し、yfinance に次回の権利落ち日があれば、
        その時期の推定を確定の日付に置き換える。
        """
        now = date.fromisoformat(today)
        start = now - timedelta(days=365)
        result = []
        for day, amount in self.history:
            ex = date.fromisoformat(day)
            if ex > now:  # 権利落ち日が未来の配当(発表済み)
                result.append(ScheduledDividend(day, amount, True))
            elif ex > start:
                shifted = _add_year(ex)
                if shifted > now:
                    result.append(ScheduledDividend(shifted.isoformat(), amount, False))
        if self.next_ex_date and self.next_ex_date > today:
            confirmed = date.fromisoformat(self.next_ex_date)
            near = [s for s in result if not s.confirmed
                    and abs((date.fromisoformat(s.ex_date) - confirmed).days) <= MATCH_DAYS]
            amount = near[0].amount if near else (self.last_amount() or 0.0)
            result = [s for s in result if s not in near[:1] and s.ex_date != self.next_ex_date]
            result.append(ScheduledDividend(self.next_ex_date, amount, True))
        end = (now + timedelta(days=366)).isoformat()
        return sorted((s for s in result if s.ex_date <= end), key=lambda s: s.ex_date)


def _add_year(day: date) -> date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:  # 2 月 29 日
        return day.replace(year=day.year + 1, day=28)


def _to_iso(value) -> str | None:
    """yfinance が返す日付(date・datetime・Timestamp・その一覧)を "YYYY-MM-DD" にする。"""
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    if hasattr(value, "date") and callable(value.date):
        value = value.date()
    return value.isoformat() if isinstance(value, date) else None


def fetch_dividend(code: str) -> DividendInfo:
    """yfinance で 1 銘柄の配当情報を取得する。取得できなければ error を入れて返す(例外は出さない)。"""
    now = datetime.now().isoformat(timespec="seconds")
    try:
        ticker = yf.Ticker(f"{code}.T")
        dividends = ticker.dividends
        if dividends is None:
            return DividendInfo(code, [], None, None, now, error="配当のデータがありません")
        history = [(f"{day:%Y-%m-%d}", round(float(amount), 4)) for day, amount in dividends.items()]
        try:
            calendar = ticker.calendar or {}
        except Exception:  # 次回の予定がない銘柄は、calendar の取得で失敗することがある
            calendar = {}
        return DividendInfo(
            code, sorted(history), _to_iso(calendar.get("Ex-Dividend Date")),
            _to_iso(calendar.get("Dividend Date")), now,
        )
    except Exception as e:  # 通信の失敗など。次の更新まで取り直さない
        return DividendInfo(code, [], None, None, now, error=f"{type(e).__name__}: {e}")


class DividendCache:
    """dividend_cache.json の読み書き。"""

    def __init__(self, path: Path = DIVIDEND_CACHE_PATH):
        self.path = path
        self.items: dict[str, DividendInfo] = {}

    def load(self) -> None:
        """ファイルを読み込む。ファイルがない・壊れているときは空のまま(キャッシュなので取り直せばよい)。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        items = data.get("items") if isinstance(data, dict) else None
        for code, saved in (items.items() if isinstance(items, dict) else []):
            info = _parse_info(code, saved)
            if info is not None:
                self.items[code] = info

    def save(self) -> None:
        data = {
            "version": FORMAT_VERSION,
            "items": {
                code: {
                    "fetched_at": i.fetched_at, "history": [list(h) for h in i.history],
                    "next_ex_date": i.next_ex_date, "next_pay_date": i.next_pay_date, "error": i.error,
                }
                for code, i in sorted(self.items.items())
            },
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(temporary, self.path)

    def get(self, code: str) -> DividendInfo | None:
        return self.items.get(code)

    def stale_codes(self, codes: list[str], refresh_days: int = REFRESH_DAYS, now: datetime | None = None) -> list[str]:
        """キャッシュにない銘柄と、取得から refresh_days 日より古い銘柄(重複なし、渡した順)。"""
        limit = ((now or datetime.now()) - timedelta(days=refresh_days)).isoformat(timespec="seconds")
        return [c for c in dict.fromkeys(codes) if c not in self.items or self.items[c].fetched_at < limit]

    def fetch(self, code: str) -> DividendInfo:
        """1 銘柄を取得してキャッシュに入れる(保存は save で行う)。"""
        info = fetch_dividend(code)
        self.items[code] = info
        return info

    def last_fetched(self) -> str:
        """もっとも新しい取得日時(なければ空文字)。"""
        return max((i.fetched_at for i in self.items.values()), default="")


def _parse_info(code, saved) -> DividendInfo | None:
    if not isinstance(code, str) or not isinstance(saved, dict) or not isinstance(saved.get("fetched_at"), str):
        return None
    history = []
    for item in saved.get("history") if isinstance(saved.get("history"), list) else []:
        if (isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)
                and isinstance(item[1], int | float) and not isinstance(item[1], bool)):
            history.append((item[0], float(item[1])))

    def text(key):
        value = saved.get(key)
        return value if isinstance(value, str) and value else None

    return DividendInfo(code, sorted(history), text("next_ex_date"), text("next_pay_date"), saved["fetched_at"],
                        text("error") or "")
