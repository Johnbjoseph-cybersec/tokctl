"""Install / uninstall tokctl hooks in ~/.claude/settings.json by read-modify-write (never whole-file overwrite)."""
from __future__ import annotations
import json
import os
import shutil
import time

SETTINGS = os.path.expanduser("~/.claude/settings.json")
HERE = os.path.dirname(os.path.abspath(__file__))
PY = "python3"
HOOKS = {
    "PostToolUse": {"matcher": "Bash", "hooks": [{"type": "command", "command": f"{PY} {HERE}/hooks/post_bash.py", "timeout": 15}]},
    "PreToolUse": {"matcher": "Read", "hooks": [{"type": "command", "command": f"{PY} {HERE}/hooks/pre_read.py", "timeout": 10}]},
    "PreCompact": {"hooks": [{"type": "command", "command": f"{PY} {HERE}/hooks/pre_compact.py", "timeout": 5}]},
    "PostToolUse:nudge": {"matcher": "*", "hooks": [{"type": "command", "command": f"{PY} {HERE}/hooks/ctx_nudge.py", "timeout": 10}]},
    "UserPromptSubmit": {"hooks": [{"type": "command", "command": f"{PY} {HERE}/hooks/prompt_ctx.py", "timeout": 10}]},
}
MARK = "/tokctl/"


def _load():
    with open(SETTINGS) as f:
        return json.load(f)


def _save(s):
    bak = SETTINGS + ".bak-tokctl-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(SETTINGS, bak)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    json.load(open(tmp))  # validate before swapping in
    os.replace(tmp, SETTINGS)
    json.load(open(SETTINGS))
    return bak


def _has(entries, needle):
    for e in entries:
        for h in e.get("hooks", []):
            if needle in h.get("command", ""):
                return True
    return False


def install(auto_compact_window: int | None = None) -> list[str]:
    s = _load()
    hooks = s.setdefault("hooks", {})
    done = []
    for ev, entry in HOOKS.items():
        ev = ev.split(":")[0]
        lst = hooks.setdefault(ev, [])
        needle = entry["hooks"][0]["command"].split()[-1]
        if not _has(lst, needle):
            lst.append(entry)
            done.append(f"{ev} hook added: {needle}")
    if auto_compact_window:
        if s.get("autoCompactWindow") != auto_compact_window:
            done.append(f"autoCompactWindow: {s.get('autoCompactWindow')} -> {auto_compact_window}")
            s["autoCompactWindow"] = auto_compact_window
    bak = _save(s)
    done.append(f"backup: {bak}")
    return done


def uninstall() -> list[str]:
    s = _load()
    done = []
    for ev, lst in list((s.get("hooks") or {}).items()):
        keep = [e for e in lst if not any(MARK in h.get("command", "") for h in e.get("hooks", []))]
        if len(keep) != len(lst):
            done.append(f"{ev}: removed {len(lst) - len(keep)} tokctl hook(s)")
            s["hooks"][ev] = keep
        if not s["hooks"][ev]:
            del s["hooks"][ev]
    if "autoCompactWindow" in s:
        done.append(f"autoCompactWindow {s['autoCompactWindow']} removed (back to default)")
        del s["autoCompactWindow"]
    _save(s)
    return done


def status() -> dict:
    s = _load()
    out = {"autoCompactWindow": s.get("autoCompactWindow")}
    for ev, entry in HOOKS.items():
        needle = entry["hooks"][0]["command"].split()[-1]
        out[os.path.basename(needle)] = _has((s.get("hooks") or {}).get(ev.split(":")[0], []), needle)
    return out
