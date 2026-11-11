#!/usr/bin/env python3
"""UserPromptSubmit: one factual line for the user when the session is heavy, so the compaction
decision stays theirs and is taken with the number in front of them. Fires when the live context
is above `prompt_ctx_min` (default 400k) and at most once per `prompt_ctx_every` prompts (default 8).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main() -> int:
    try:
        from tokctl import store
        from tokctl.hooks.ctx_nudge import tail_turns
        d = json.load(sys.stdin)
        if not store.enabled():
            return 0
        cfg = store.config()
        if not cfg.get("prompt_ctx", True):
            return 0
        sid = d.get("session_id", "?")
        state_p = os.path.join(store.STATE, "prompt-state.json")
        try:
            st = json.load(open(state_p))
        except Exception:
            st = {}
        s = st.get(sid) or {"n": 0, "last": -100}
        s["n"] += 1
        out = None
        if s["n"] - s["last"] >= int(cfg.get("prompt_ctx_every", 8)):
            ctx, _ = tail_turns(d.get("transcript_path", ""))
            if ctx >= int(cfg.get("prompt_ctx_min", 400_000)):
                s["last"] = s["n"]
                out = (f"[tokctl] This session's context is {ctx // 1000}k tokens; every reply re-reads it, so a turn here costs about "
                       f"{max(1, ctx // 80_000)}x a fresh session's turn. If the next task is unrelated to this one, a new session or /compact keeps "
                       f"quality and cuts that cost. If it continues this work, carry on; nothing is lost by staying.")
        st[sid] = s
        store.ensure()
        json.dump(st, open(state_p, "w"))
        if out:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": out}}))
        return 0
    except Exception as e:
        try:
            with open(os.path.join(os.environ.get("TOKCTL_STATE") or os.path.expanduser("~/.claude/tokctl"), "hook-errors.log"), "a") as f:
                f.write(f"prompt_ctx: {type(e).__name__}: {e}\n")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
