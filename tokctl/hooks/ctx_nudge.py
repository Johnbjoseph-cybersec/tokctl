#!/usr/bin/env python3
"""PostToolUse(*): a context-size fact, injected rarely, when it changes the right move.

Every assistant turn re-reads the whole context. Three single-call turns at 500k tokens cost
1.5M cache reads where one batched turn costs 500k. This hook injects one short line when:
  - the live context (from the transcript's last usage fields) is above `ctx_nudge_min` (default 200k), and
  - the last `ctx_nudge_single` turns (default 3) each carried exactly one tool call,
and at most once per `ctx_nudge_every` tool calls (default 25). Lossless: it adds ~70 tokens of
fact and changes nothing else.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def tail_turns(transcript_path: str, n: int = 6):
    """Return (context_tokens, [tool calls per assistant turn for the last n turns])."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            f.seek(max(0, size - 400_000))
            lines = f.read().decode("utf-8", "ignore").split("\n")
    except Exception:
        return 0, []
    ctx, turns, order = 0, {}, []
    for l in lines:
        if '"type":"assistant"' not in l and '"type": "assistant"' not in l:
            continue
        try:
            d = json.loads(l)
        except Exception:
            continue
        m = d.get("message") or {}
        u = m.get("usage") or {}
        if u:
            ctx = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
        # Claude Code writes one record per content block; one API turn shares a requestId
        key = d.get("requestId") or d.get("uuid") or f"_{len(order)}"
        if key not in turns:
            turns[key] = 0
            order.append(key)
        turns[key] += sum(1 for b in (m.get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use")
    per_turn = [turns[k] for k in order]
    return ctx, per_turn[-n:]


HANDOFF = ("[tokctl] You are a subagent and your context is {ctx}k tokens; every further step re-reads all of it and the "
           "orchestrator pays for it. At the next clean boundary (a passing check, a saved file), finish: write a handoff "
           "that a fresh agent can continue from, as your final result or in {path}: DONE (verified how), IN FLIGHT (exact step), "
           "NOT STARTED, DECISIONS and why, FILES touched (absolute paths), NUMBERS, NEXT ACTION. Then return. Do not abandon a step mid-edit. "
           "If you must return a manifest (Forge maker), set blocked to ['handoff: continue from {path}'] so the next round continues from it.")


def handoff(d, cfg, store, sid, agent) -> int:
    """Subagent mode: when a lane's context is heavy, ask it to hand off cleanly instead of running to the ceiling."""
    if not cfg.get("agent_handoff", True):
        return 0
    state_p = os.path.join(store.STATE, "handoff-state.json")
    try:
        st = json.load(open(state_p))
    except Exception:
        st = {}
    key = f"{sid}:{agent}"
    s = st.get(key) or {"calls": 0, "last": -10_000}
    s["calls"] += 1
    fire = False
    if s["calls"] - s["last"] >= int(cfg.get("agent_handoff_every", 15)):
        ctx, _ = tail_turns(d.get("transcript_path", ""), 1)
        if ctx >= int(cfg.get("agent_handoff_min", 350_000)):
            fire = True
            s["last"] = s["calls"]
    st[key] = s
    if len(st) > 300:
        st = dict(sorted(st.items(), key=lambda kv: kv[1]["calls"])[-150:])
    store.ensure()
    json.dump(st, open(state_p, "w"))
    if fire:
        store.ledger({"session": sid, "tool": "nudge", "class": "handoff", "ctx": ctx, "agent": agent})
        path = os.path.join(d.get("cwd") or ".", "HANDOFF-" + str(agent)[:12] + ".md")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": HANDOFF.format(ctx=ctx // 1000, path=path)}}))
    return 0


def main() -> int:
    try:
        from tokctl import store
        d = json.load(sys.stdin)
        if not store.enabled():
            return 0
        cfg = store.config()
        if not cfg.get("ctx_nudge", True):
            return 0
        sid = d.get("session_id", "?")
        agent = d.get("agent_id") or d.get("agent_type")
        if agent:
            return handoff(d, cfg, store, sid, agent)
        state_p = os.path.join(store.STATE, "nudge-state.json")
        try:
            st = json.load(open(state_p))
        except Exception:
            st = {}
        s = st.get(sid) or {"calls": 0, "last": -10_000}
        s["calls"] += 1
        fire = False
        ctx, per_turn = 0, []
        every = int(cfg.get("ctx_nudge_every", 25))
        if s["calls"] - s["last"] >= every:
            ctx, per_turn = tail_turns(d.get("transcript_path", ""))
            single = int(cfg.get("ctx_nudge_single", 3))
            if ctx >= int(cfg.get("ctx_nudge_min", 200_000)) and len(per_turn) >= single and all(c == 1 for c in per_turn[-single:]):
                fire = True
                s["last"] = s["calls"]
        st[sid] = s
        if len(st) > 200:  # keep the state file small
            st = dict(sorted(st.items(), key=lambda kv: kv[1]["calls"])[-100:])
        store.ensure()
        json.dump(st, open(state_p, "w"))
        if fire:
            store.ledger({"session": sid, "tool": "nudge", "class": "ctx", "ctx": ctx})
            msg = (f"[tokctl] Context is {ctx // 1000}k tokens and every turn re-reads all of it; the last {len(per_turn)} turns made one tool call each. "
                   f"Send independent calls together in one message. For a multi-step search or read-through, delegate to an Explore or general-purpose agent "
                   f"(its context starts near zero) and keep only the conclusion.")
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))
        return 0
    except Exception as e:
        try:
            with open(os.path.join(os.environ.get("TOKCTL_STATE") or os.path.expanduser("~/.claude/tokctl"), "hook-errors.log"), "a") as f:
                f.write(f"ctx_nudge: {type(e).__name__}: {e}\n")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
