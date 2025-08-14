# app/services/metrics.py
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

_ALMATY_TZ = timezone(timedelta(hours=6))

def _today_ymd() -> str:
    return datetime.now(_ALMATY_TZ).strftime("%Y%m%d")

def _fmt_tags(tags: Optional[Dict[str, str]]) -> str:
    if not tags:
        return ""
    items = sorted((k, str(v)) for k, v in tags.items() if v is not None)
    return ",".join(f"{k}={v}" for k, v in items)

class _InMemoryStore:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._counts: Dict[str, Dict[str, int]] = {}
        self._sums: Dict[str, Dict[str, float]] = {}
        self._nums: Dict[str, Dict[str, int]] = {}

    async def incr(self, date_key: str, field: str, n: int = 1):
        async with self._lock:
            self._counts.setdefault(date_key, {})
            self._counts[date_key][field] = self._counts[date_key].get(field, 0) + n

    async def add_sample(self, date_key: str, field: str, value: float):
        async with self._lock:
            self._sums.setdefault(date_key, {})
            self._nums.setdefault(date_key, {})
            self._sums[date_key][field] = self._sums[date_key].get(field, 0.0) + float(value)
            self._nums[date_key][field] = self._nums[date_key].get(field, 0) + 1

    async def snapshot_today(self):
        date_key = _today_ymd()
        async with self._lock:
            counts = self._counts.get(date_key, {}).copy()
            sums = self._sums.get(date_key, {}).copy()
            nums = self._nums.get(date_key, {}).copy()
        return counts, sums, nums

class Metrics:
    def __init__(self):
        self._store = _InMemoryStore()

    async def init(self):
        return  # no-op для совместимости

    async def inc(self, metric: str, *, tags: Optional[Dict[str, str]] = None, n: int = 1):
        field = f"{metric}|{_fmt_tags(tags)}"
        await self._store.incr(_today_ymd(), field, n)

    async def observe_ms(self, metric: str, value_ms: float, *, tags: Optional[Dict[str, str]] = None):
        field = f"{metric}|{_fmt_tags(tags)}"
        await self._store.add_sample(_today_ymd(), field, float(value_ms))

    async def snapshot(self, days: int = 1):
        counts, sums, nums = await self._store.snapshot_today()
        avg_ms: Dict[str, float] = {}
        for field, s in sums.items():
            n = nums.get(field, 0)
            if n > 0:
                avg_ms[field] = round(s / n, 1)
        return {"counts": counts, "avg_ms": avg_ms}

METRICS = Metrics()

async def inc_metric(name: str, **kw):      await METRICS.inc(name, **kw)
async def observe_latency_ms(name: str, ms: float, **kw): await METRICS.observe_ms(name, ms, **kw)
