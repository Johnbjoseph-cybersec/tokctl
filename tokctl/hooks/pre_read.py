#!/usr/bin/env python3
"""PreToolUse(Read): refuse an identical re-read of an unchanged file within the recent window.

Measured: 16% of Read calls re-read a path already read in the same session. The deny
reason tells the model exactly when it last read the file. The gate is conservative:
  - same path AND same offset/limit only,
  - file mtime older than that earlier read (unchanged),
  - earlier read within `read_dedupe_window` tool calls (default 40),
  - never across a compaction boundary (the content is gone from context then).
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

COMPACT_RX = re.compile(r'"isCompactSummary":\s*true|"subtype":\s*"compact_boundary"|"type":\s*"summary"')


def recent_reads(transcript_path: str, window: int):
    """Yield (path, offset, limit, ts, calls_ago) for Read tool_uses after the last compaction."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            f.seek(max(0, size - 1_500_000))
            lines = f.read().decode("utf-8", "ignore").split("\n")
    except Exception:
        return []
    tools = []  # (name, input, ts)
    for l in lines:
        if COMPACT_RX.search(l):
            tools = []
            continue
        if '"tool_use"' not in l:
            continue
        try:
            d = json.loads(l)
        except Exception:
            continue
        if d.get("isSidechain") or d.get("type") != "assistant":
            continue
        for b in (d.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tools.append((b.get("name"), b.get("input") or {}, d.get("timestamp", "")))
    out = []
    n = len(tools)
    for i, (name, inp, ts) in enumerate(tools):
        ago = n - i
        if name == "Read" and ago <= window:
            out.append((inp.get("file_path"), inp.get("offset"), inp.get("limit"), ts, ago))
    return out


def iso_to_epoch(ts: str) -> float:
    try:
        from datetime import datetime, timezone
        return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def main() -> int:
    try:
        from tokctl import store
        d = json.load(sys.stdin)
        if d.get("tool_name") != "Read" or not store.enabled():
            return 0
        cfg = store.config()
        if not cfg.get("read_dedupe", True):
            return 0
        inp = d.get("tool_input") or {}
        path = inp.get("file_path")
        if not path or not os.path.isfile(path):
            return 0
        reads = recent_reads(d.get("transcript_path", ""), int(cfg.get("read_dedupe_window", 40)))
        mtime = os.path.getmtime(path)
        for p, off, lim, ts, ago in reads:
            if p == path and off == inp.get("offset") and lim == inp.get("limit"):
                t = iso_to_epoch(ts)
                if t and mtime < t:
                    when = time.strftime("%H:%M:%S", time.localtime(t))
                    rng = f" (offset={off}, limit={lim})" if off or lim else ""
                    reason = (f"tokctl: you read {path}{rng} at {when}, {ago} tool calls ago, and the file has not changed since "
                              f"(mtime older than that read). Reuse the content already in context. If you need a different part, read it with offset/limit.")
                    store.ledger({"session": d.get("session_id"), "tool": "Read", "class": "dedupe", "path": path, "ago": ago, "mode": cfg.get("mode")})
                    if cfg.get("mode", "enforce") != "enforce":
                        return 0
                    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))
                    return 0
        return 0
    except Exception as e:
        try:
            with open(os.path.join(os.environ.get("TOKCTL_STATE") or os.path.expanduser("~/.claude/tokctl"), "hook-errors.log"), "a") as f:
                f.write(f"pre_read: {type(e).__name__}: {e}\n")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
