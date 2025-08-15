import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any

from redis import Redis
from app.settings import settings

TZ = ZoneInfo(settings.tz)

DEFAULT_STATS = {
    "tests": 0,
    "brands": {},
    "points": 0,
    "last": "",
    "best_truth": 0,
    "best_assoc": 0,
    "best_blitz": 0,
}

def _now_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def _stats_key(uid: int, period: str = "total") -> str:
    if period == "daily":
        day = datetime.now(TZ).strftime("%Y-%m-%d")
        return f"user:{uid}:stats:daily:{day}"
    return f"user:{uid}:stats"

class MemoryRedis:
    def __init__(self) -> None:
        self.data: Dict[str, str] = {}
        # значения могут быть как int, так и float (для латенсий)
        self.hashes: Dict[str, Dict[str, float]] = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value

    def hincrby(self, name: str, key: str, amount: int) -> None:
        h = self.hashes.setdefault(name, {})
        h[key] = float(h.get(key, 0)) + int(amount)

    def hincrbyfloat(self, name: str, key: str, amount: float) -> None:
        h = self.hashes.setdefault(name, {})
        h[key] = float(h.get(key, 0)) + float(amount)

    def hgetall(self, name: str) -> Dict[str, float]:
        return self.hashes.get(name, {}).copy()

    def keys(self, pattern: str):
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k for k in self.hashes.keys() if k.startswith(prefix)]
        return [pattern] if pattern in self.hashes else []

    def scan_iter(self, pattern: str):
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return (k for k in self.data.keys() if k.startswith(prefix))
        return iter([pattern]) if pattern in self.data else iter([])

    def exists(self, key: str) -> bool:
        return key in self.data

# Init Redis (если не доступен — in-memory заглушка)
try:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    redis.ping()
except Exception as e:
    logging.warning("Redis unavailable, using in-memory store: %s", e)
    redis = MemoryRedis()

def get_stats(user_id: int, period: str = "total") -> Dict[str, Any]:
    key = _stats_key(user_id, period)
    data = redis.get(key)
    if data is None:
        redis.set(key, json.dumps(DEFAULT_STATS))
        return DEFAULT_STATS.copy()
    st = json.loads(data)
    st.setdefault("brands", {})
    return st

def save_stats(user_id: int, stats: Dict[str, Any], period: str = "total") -> None:
    key = _stats_key(user_id, period)
    redis.set(key, json.dumps(stats))

def record_history(event: str) -> None:
    now = datetime.now(TZ)
    day_key = now.strftime("%Y-%m-%d")
    redis.hincrby(f"history:daily:{day_key}", event, 1)
    redis.hincrby("history:total", event, 1)

def format_activity(period: str, limit: int = 10) -> str:
    if period == "daily":
        keys = sorted(redis.keys("history:daily:*"), reverse=True)[:limit]
        lines = []
        for k in keys:
            day = k.split(":")[-1]
            data = redis.hgetall(k)
            lines.append(
                f"{day}: тесты {int(data.get('tests', 0))}, верю {int(data.get('truth', 0))}, "
                f"ассоциации {int(data.get('assoc', 0))}, блиц {int(data.get('blitz', 0))}, "
                f"бренды {int(data.get('brands', 0))}"
            )
        return "\n".join(lines)
    elif period == "total":
        data = redis.hgetall("history:total")
        if not data:
            return ""
        return (
            f"Всего: тесты {int(data.get('tests', 0))}, верю {int(data.get('truth', 0))}, "
            f"ассоциации {int(data.get('assoc', 0))}, блиц {int(data.get('blitz', 0))}, "
            f"бренды {int(data.get('brands', 0))}"
        )
    return ""

def record_brand_view(user_id: int, brand: str, category: str) -> None:
    for period in ("total", "daily"):
        stats = get_stats(user_id, period)
        if brand not in stats["brands"]:
            stats["brands"][brand] = category
        stats["last"] = _now_str()
        save_stats(user_id, stats, period)
    record_history("brands")

def record_test_result(user_id: int, points: int) -> None:
    for period in ("total", "daily"):
        stats = get_stats(user_id, period)
        stats["tests"] = stats.get("tests", 0) + 1
        stats["points"] = stats.get("points", 0) + points
        stats["last"] = _now_str()
        save_stats(user_id, stats, period)
    record_history("tests")

def record_truth_result(user_id: int, points: int) -> int:
    best = 0
    for period in ("total", "daily"):
        stats = get_stats(user_id, period)
        if points > stats.get("best_truth", 0):
            stats["best_truth"] = points
        stats["points"] = stats.get("points", 0) + points
        stats["last"] = _now_str()
        save_stats(user_id, stats, period)
        if period == "total":
            best = stats["best_truth"]
    record_history("truth")
    return best

def record_assoc_result(user_id: int, points: int) -> int:
    best = 0
    for period in ("total", "daily"):
        stats = get_stats(user_id, period)
        if points > stats.get("best_assoc", 0):
            stats["best_assoc"] = points
        stats["points"] = stats.get("points", 0) + points
        stats["last"] = _now_str()
        save_stats(user_id, stats, period)
        if period == "total":
            best = stats["best_assoc"]
    record_history("assoc")
    return best

def record_blitz_result(user_id: int, points: int) -> int:
    best = 0
    for period in ("total", "daily"):
        stats = get_stats(user_id, period)
        if points > stats.get("best_blitz", 0):
            stats["best_blitz"] = points
        stats["points"] = stats.get("points", 0) + points
        stats["last"] = _now_str()
        save_stats(user_id, stats, period)
        if period == "total":
            best = stats["best_blitz"]
    record_history("blitz")
    return best

# --- NEW: форматирование тегов для ключей метрик
def _fmt_tags(tags: Dict[str, Any] | None) -> str:
    if not tags:
        return ""
    items = sorted((str(k), str(v)) for k, v in tags.items() if v is not None)
    return ",".join(f"{k}={v}" for k, v in items)

# =========================
# AI metrics (счётчики и средние времена)
# =========================

def _ai_count_key(period: str) -> str:
    if period == "daily":
        day_key = datetime.now(TZ).strftime("%Y-%m-%d")
        return f"ai:count:daily:{day_key}"
    return "ai:count:total"

def _ai_sum_key(period: str) -> str:
    if period == "daily":
        day_key = datetime.now(TZ).strftime("%Y-%m-%d")
        return f"ai:sum:daily:{day_key}"
    return "ai:sum:total"

def _ai_num_key(period: str) -> str:
    if period == "daily":
        day_key = datetime.now(TZ).strftime("%Y-%m-%d")
        return f"ai:num:daily:{day_key}"
    return "ai:num:total"

def ai_inc(event: str, *, tags: Dict[str, Any] | None = None, n: int = 1) -> None:
    """
    Счётчик событий ИИ. Пример:
      ai_inc("ai.enter", tags={"how": "button"})
      ai_inc("ai.source", tags={"source": "web"})
    Пишем и в daily, и в total.
    """
    field = f"{event}|{_fmt_tags(tags)}"
    for period in ("daily", "total"):
        key = _ai_count_key(period)
        redis.hincrby(key, field, n)

def ai_observe_ms(metric: str, value_ms: float, *, tags: Dict[str, Any] | None = None) -> None:
    """
    Накопление сумм и количеств для средних времен (ms).
    Пример:
      ai_observe_ms("ai.latency", 1432.7, tags={"intent":"brand","source":"web"})
    """
    field = f"{metric}|{_fmt_tags(tags)}"
    for period in ("daily", "total"):
        key_s = _ai_sum_key(period)
        key_n = _ai_num_key(period)
        # и реальный Redis, и MemoryRedis поддерживают этот метод
        redis.hincrbyfloat(key_s, field, float(value_ms))
        redis.hincrby(key_n, field, 1)

def format_ai_stats(period: str = "daily", top: int = 20) -> str:
    """
    Красивый текст для /stats_ai: топ счётчиков и средние времена.
    """
    counts = redis.hgetall(_ai_count_key(period))
    if counts:
        count_items = sorted(counts.items(), key=lambda kv: int(kv[1]), reverse=True)[:top]
        counts_str = "\n".join(f"• {k} — {int(v)}" for k, v in count_items)
    else:
        counts_str = "—"

    sums = redis.hgetall(_ai_sum_key(period))
    nums = redis.hgetall(_ai_num_key(period))
    avg: list[tuple[str, float]] = []
    for field, s in sums.items():
        n = int(nums.get(field, 0))
        if n > 0:
            avg.append((field, round(float(s) / n, 1)))
    avg.sort(key=lambda kv: kv[1])  # от самых быстрых
    avg_str = "\n".join(f"• {k} — {v} ms" for k, v in avg[:top]) if avg else "—"

    title = "Ежедневно" if period == "daily" else "Итого"
    return f"<b>💡 AI-метрики — {title}</b>\n\n<b>События</b>:\n{counts_str}\n\n<b>Среднее время</b>:\n{avg_str}"
