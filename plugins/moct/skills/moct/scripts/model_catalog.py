#!/usr/bin/env python3
"""Summarize the host Codex model catalog without calling any model."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}\Z")
EFFORT = re.compile(r"[a-z][a-z0-9_-]{0,30}\Z")
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_AGE = timedelta(hours=24)


def read_cache(path):
    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


def compact_models(data):
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return []
    result, seen = [], set()
    for model in data["models"][:500]:
        if not isinstance(model, dict) or model.get("visibility") != "list":
            continue
        slug = model.get("slug")
        if not isinstance(slug, str) or not ID.fullmatch(slug) or slug in seen:
            continue
        seen.add(slug)
        levels = model.get("supported_reasoning_levels")
        levels = levels if isinstance(levels, list) else []
        efforts = [level["effort"] for level in levels if isinstance(level, dict)
                   and isinstance(level.get("effort"), str) and EFFORT.fullmatch(level["effort"])]
        display = model.get("display_name")
        description = model.get("description")
        result.append({"id": slug,
                       "display_name": display[:120] if isinstance(display, str) else slug,
                       "description": description[:500] if isinstance(description, str) else "",
                       "supported_efforts": efforts})
    return result


def freshness(timestamp, now):
    if not isinstance(timestamp, str):
        return "unknown"
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return "unknown"
        age = now - parsed.astimezone(timezone.utc)
        if age < -timedelta(minutes=5):
            return "unknown"
        return "recent" if age <= MAX_AGE else "stale"
    except (ValueError, OverflowError):
        return "unknown"


def inspect(cache_path, now=None):
    now = now or datetime.now(timezone.utc)
    source, cli_data = "unavailable", None
    try:
        result = subprocess.run(["codex", "debug", "models"], capture_output=True,
                                text=True, timeout=8, check=False)
        if result.returncode == 0 and len(result.stdout) <= MAX_CACHE_BYTES:
            value = json.loads(result.stdout)
            if compact_models(value):
                source, cli_data = "codex_cli_catalog", value
    except (OSError, UnicodeError, ValueError, RecursionError, subprocess.TimeoutExpired):
        pass

    cache = read_cache(cache_path)
    cache_models = compact_models(cache)
    models = compact_models(cli_data)
    if not models and cache_models:
        source, models = "local_cache", cache_models
    timestamp = cache.get("fetched_at") if isinstance(cache, dict) else None
    # A CLI catalog may be bundled, account-specific, or drawn from a different
    # cache. Only attach the cache timestamp if the visible model IDs match.
    if source == "codex_cli_catalog" and {m["id"] for m in models} != {m["id"] for m in cache_models}:
        timestamp = None
    age_status = freshness(timestamp, now)
    return {
        "source": source,
        "cache_fetched_at_utc": timestamp,
        "cache_age": age_status,
        "models": models,
        "limitations": [
            "CLI catalog or cache can differ from the active desktop model picker; it is not proof of current access or price.",
            "No price data is included; check current official Codex pricing separately.",
        ],
    }


def main():
    codex_dir = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    print(json.dumps(inspect(codex_dir / "models_cache.json"), ensure_ascii=False))


if __name__ == "__main__":
    main()
