#!/usr/bin/env python3
"""PostToolUse(Bash): replace a noisy result with its governed form; keep the full text on disk.

Never fails the tool: any exception exits 0 with no output. Kill switch: ~/.claude/tokctl/OFF.
Mode `shadow` logs what it would have saved and changes nothing.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main() -> int:
    try:
        from tokctl import store, filter as flt
        d = json.load(sys.stdin)
        if d.get("tool_name") != "Bash" or not store.enabled():
            return 0
        tr = d.get("tool_response")
        if not isinstance(tr, dict):
            return 0
        stdout = tr.get("stdout") or ""
        cfg = store.config()
        if len(stdout) < cfg.get("min_chars", flt.MIN_CHARS):
            return 0
        cmd = (d.get("tool_input") or {}).get("command", "")
        res = flt.govern(cmd, stdout, cfg.get("caps") or None)
        saved = res.in_chars - res.out_chars
        if saved <= 0 and not res.transforms:
            return 0
        sid = store.spill(d.get("session_id", "?"), d.get("tool_use_id", "?"), cmd, stdout, {"class": res.klass, "shown": res.out_chars})
        mode = cfg.get("mode", "enforce")
        store.ledger({"session": d.get("session_id"), "tool": "Bash", "class": res.klass, "in": res.in_chars, "out": res.out_chars, "mode": mode, "spill": sid, "cwd": d.get("cwd"), "agent": d.get("agent_id") or d.get("agent_type")})
        if mode != "enforce":
            return 0
        new = dict(tr)
        new["stdout"] = res.text + ("\n" + flt.footer(res, sid) if flt.footer(res, sid) else "")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": new}}))
        return 0
    except Exception as e:  # never break the tool call
        try:
            with open(os.path.join(os.environ.get("TOKCTL_STATE") or os.path.expanduser("~/.claude/tokctl"), "hook-errors.log"), "a") as f:
                f.write(f"post_bash: {type(e).__name__}: {e}\n")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
