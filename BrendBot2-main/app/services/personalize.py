# app/services/personalize.py
from __future__ import annotations
from typing import Optional, Dict, Any
import json
from pathlib import Path

try:
    from redis import Redis
    _redis = Redis(host="localhost", port=6379, db=0)
    _redis.ping()
except Exception:
    _redis = None

STORE = Path("data/user_prefs.json")

def _load() -> Dict[str, Any]:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save(d: Dict[str, Any]):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def set_pref(user_id: int, key: str, value: str):
    if _redis:
        _redis.hset(f"user:{user_id}:prefs", key, value)
        return
    data = _load()
    u = data.get(str(user_id), {})
    u[key] = value
    data[str(user_id)] = u
    _save(data)

def get_pref(user_id: int, key: str, default: Optional[str] = None) -> Optional[str]:
    if _redis:
        res = _redis.hget(f"user:{user_id}:prefs", key)
        return res.decode() if res else default
    data = _load()
    return data.get(str(user_id), {}).get(key, default)
