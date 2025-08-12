
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
        self.hashes: Dict[str, Dict[str, int]] = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value

    def hincrby(self, name: str, key: str, amount: int) -> None:
        h = self.hashes.setdefault(name, {})
        h[key] = h.get(key, 0) + amount

    def hgetall(self, name: str) -> Dict[str, int]:
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

# Init Redis
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
                f"{day}: тесты {data.get('tests', 0)}, верю {data.get('truth', 0)}, "
                f"ассоциации {data.get('assoc', 0)}, блиц {data.get('blitz', 0)}, "
                f"бренды {data.get('brands', 0)}"
            )
        return "\n".join(lines)
    elif period == "total":
        data = redis.hgetall("history:total")
        if not data:
            return ""
        return (
            f"Всего: тесты {data.get('tests', 0)}, верю {data.get('truth', 0)}, "
            f"ассоциации {data.get('assoc', 0)}, блиц {data.get('blitz', 0)}, "
            f"бренды {data.get('brands', 0)}"
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
