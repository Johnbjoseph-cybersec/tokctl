"""State: config, kill switch, spill store (full outputs on disk), ledger (measured savings)."""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import time

STATE = os.environ.get("TOKCTL_STATE") or os.path.expanduser("~/.claude/tokctl")
SPILL = os.path.join(STATE, "spill")
LEDGER = os.path.join(STATE, "ledger.jsonl")
CONFIG = os.path.join(STATE, "config.json")
OFF = os.path.join(STATE, "OFF")
RETAIN_DAYS = 7

DEFAULTS = {"mode": "enforce", "caps": {}, "read_dedupe": True, "read_dedupe_window": 40, "min_chars": 1500}


def ensure():
    os.makedirs(SPILL, exist_ok=True)


def config() -> dict:
    try:
        with open(CONFIG) as f:
            return {**DEFAULTS, **json.load(f)}
    except Exception:
        return dict(DEFAULTS)


def save_config(c: dict):
    ensure()
    with open(CONFIG, "w") as f:
        json.dump(c, f, indent=1)


def enabled() -> bool:
    return not os.path.exists(OFF)


def mode() -> str:
    if not enabled():
        return "off"
    return config().get("mode", "enforce")


def spill_id(session_id: str, tool_use_id: str) -> str:
    return hashlib.sha1(f"{session_id}:{tool_use_id}".encode()).hexdigest()[:6]


def spill(session_id: str, tool_use_id: str, command: str, text: str, meta: dict | None = None) -> str:
    ensure()
    day = time.strftime("%Y-%m-%d")
    d = os.path.join(SPILL, day)
    os.makedirs(d, exist_ok=True)
    sid = spill_id(session_id, tool_use_id)
    with open(os.path.join(d, sid + ".out"), "w", errors="replace") as f:
        f.write(text)
    with open(os.path.join(d, sid + ".json"), "w") as f:
        json.dump({"ts": time.time(), "session": session_id, "tool_use_id": tool_use_id, "command": command[:2000], "chars": len(text), **(meta or {})}, f)
    _maybe_prune()
    return sid


def find_spill(sid: str) -> tuple[str, dict] | None:
    if not os.path.isdir(SPILL):
        return None
    for day in sorted(os.listdir(SPILL), reverse=True):
        p = os.path.join(SPILL, day, sid + ".out")
        if os.path.exists(p):
            try:
                meta = json.load(open(p[:-4] + ".json"))
            except Exception:
                meta = {}
            return open(p, errors="replace").read(), meta
    return None


def _maybe_prune():
    marker = os.path.join(STATE, ".pruned")
    try:
        if os.path.exists(marker) and time.time() - os.path.getmtime(marker) < 86400:
            return
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - RETAIN_DAYS * 86400))
        for day in os.listdir(SPILL):
            if re.fullmatch(r"\d{4}-\d\d-\d\d", day) and day < cutoff:
                shutil.rmtree(os.path.join(SPILL, day), ignore_errors=True)
        open(marker, "w").close()
    except Exception:
        pass


def ledger(entry: dict):
    ensure()
    entry = {"ts": time.time(), **entry}
    with open(LEDGER, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_ledger():
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER) as f:
        for l in f:
            try:
                out.append(json.loads(l))
            except Exception:
                continue
    return out
