"""Measure where tokens go, from the `usage` fields Claude Code writes into every transcript.

Billed tokens come from the API's own accounting (input, cache_creation, cache_read, output),
not from estimates. Attribution of what fills the context uses chars/4 and is labelled as such.
"""
from __future__ import annotations
import collections
import glob
import json
import os
import re
import statistics
import time

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")


def _clen(c) -> int:
    if isinstance(c, str):
        return len(c)
    n = 0
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    n += len(b.get("text", ""))
                elif b.get("type") == "image":
                    n += 6000  # ~1,500 tokens per image, expressed in chars
            elif isinstance(b, str):
                n += len(b)
    return n


def _bash_head(cmd: str) -> str:
    cmd = re.sub(r"^(\s*cd\s+(\"[^\"]*\"|'[^']*'|\S+)\s*(&&|;|\n)\s*)+", "", cmd.strip())
    parts = cmd.split()
    if not parts:
        return "?"
    hw = parts[0]
    if hw in ("python3", "python", "node", "bash", "sh", "git", "docker", "npm", "gh") and len(parts) > 1:
        hw += " " + os.path.basename(parts[1])[:20]
    return hw[:40]


def session_files(only_sessions: set | None = None):
    for f in glob.glob(os.path.join(PROJECTS, "**", "*.jsonl"), recursive=True):
        if only_sessions is not None:
            rel = f.split("/projects/", 1)[1].split("/")
            sid = os.path.basename(f)[:-6]
            parent = rel[1] if len(rel) > 2 else sid
            if sid not in only_sessions and parent not in only_sessions:
                continue
        yield f


def scan(since: str, only_sessions: set | None = None, compact_whatif=(300_000, 400_000, 500_000, 600_000)) -> dict:
    usage = collections.Counter()
    turns = 0
    sessions = 0
    tool_cnt = collections.Counter()
    tool_chars = collections.Counter()
    tool_sizes = collections.defaultdict(list)
    bash_head = collections.Counter()
    bash_head_chars = collections.Counter()
    read_ext = collections.Counter()
    reads = reread = 0
    asst_text = asst_think = john = sysrem = 0
    skills = collections.Counter()
    agents = collections.Counter()
    firsts, turns_per, integral = [], [], 0
    whatif = {w: 0 for w in compact_whatif}
    reread_mult_num = reread_mult_den = 0
    seen_req = set()
    sub_bill = sub_turns = 0
    sub_runs = []  # billed per subagent run
    for f in session_files(only_sessions):
        idmap, seen, ctxs, used = {}, set(), [], False
        run_bill = 0
        results_at = []  # (turn index, chars) to compute re-read multiplier
        try:
            fh = open(f, errors="ignore")
        except Exception:
            continue
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("timestamp", "")
            if ts[:10] < since:
                continue
            m = d.get("message") or {}
            t = d.get("type")
            if d.get("isSidechain") or f.count("/") > PROJECTS.count("/") + 2:
                if t == "assistant":
                    u = m.get("usage") or {}
                    r = d.get("requestId")
                    if u and (not r or r not in seen_req):
                        if r:
                            seen_req.add(r)
                        c = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("output_tokens") or 0)
                        sub_bill += c
                        sub_turns += 1
                        run_bill += c
                continue
            used = True
            if t == "assistant":
                u = m.get("usage") or {}
                if u:
                    turns += 1
                    for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
                        usage[k] += u.get(k) or 0
                    ctxs.append((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0))
                for b in m.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text":
                        asst_text += len(b.get("text", ""))
                    elif bt == "thinking":
                        asst_think += len(b.get("thinking", ""))
                    elif bt == "tool_use":
                        name = b.get("name", "?")
                        inp = b.get("input") or {}
                        summ = ""
                        if name == "Bash":
                            summ = _bash_head(inp.get("command", ""))
                        elif name in ("Read", "Edit", "Write"):
                            summ = inp.get("file_path", "")
                            if name == "Read":
                                reads += 1
                                if summ in seen:
                                    reread += 1
                                seen.add(summ)
                        elif name == "Skill":
                            skills[inp.get("skill", "?")] += 1
                        elif name in ("Agent", "Task"):
                            agents[inp.get("subagent_type", "general-purpose")] += 1
                        idmap[b.get("id")] = (name, summ)
            elif t == "user":
                c = m.get("content")
                if isinstance(c, str):
                    if c.startswith("<system-reminder>") or c.startswith("<command-") or c.startswith("<local-command"):
                        sysrem += len(c)
                    else:
                        john += len(c)
                elif isinstance(c, list):
                    for b in c:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_result":
                            n = _clen(b.get("content"))
                            name, summ = idmap.get(b.get("tool_use_id"), ("?", ""))
                            tool_cnt[name] += 1
                            tool_chars[name] += n
                            tool_sizes[name].append(n)
                            results_at.append((len(ctxs), n))
                            if name == "Bash":
                                bash_head[summ] += 1
                                bash_head_chars[summ] += n
                            if name == "Read":
                                read_ext[os.path.splitext(summ)[1] or "(none)"] += n
                        elif b.get("type") == "text":
                            tx = b.get("text", "")
                            if "<system-reminder>" in tx[:60]:
                                sysrem += len(tx)
                            else:
                                john += len(tx)
        if run_bill:
            sub_runs.append(run_bill)
        if used and ctxs:
            sessions += 1
            firsts.append(ctxs[0])
            turns_per.append(len(ctxs))
            integral += sum(ctxs)
            for ti, n in results_at:
                reread_mult_num += n * (len(ctxs) - ti)
                reread_mult_den += n
            base = min(ctxs[0], 120_000)
            for w in whatif:
                # model: whenever context passes w, it is reset to base + 40k summary and keeps growing by the same deltas
                cur = ctxs[0]
                tot = cur
                for a, b in zip(ctxs, ctxs[1:]):
                    delta = max(b - a, 0)
                    cur = cur + delta
                    if cur > w:
                        cur = base + 40_000 + delta
                    tot += cur
                whatif[w] += tot
    tot_tool = sum(tool_chars.values())
    billed = sum(usage.values())
    sub_runs.sort()
    top10 = sum(sub_runs[int(len(sub_runs) * 0.9):]) if sub_runs else 0
    sub = {"billed": sub_bill, "turns": sub_turns, "runs": len(sub_runs), "top10pct_runs_share": round(100 * top10 / max(sub_bill, 1)),
           "share_of_everything": round(100 * sub_bill / max(sub_bill + billed, 1))}
    fills = {"tool results": tot_tool, "user typed/pasted": john, "assistant text": asst_text, "assistant thinking": asst_think, "system reminders/hooks": sysrem}
    firsts.sort()
    turns_per.sort()
    sz_all = sorted(x for v in tool_sizes.values() for x in v)
    by_tool = []
    for name, ch in tool_chars.most_common(16):
        s = sorted(tool_sizes[name])
        by_tool.append({"tool": name, "n": tool_cnt[name], "tokens": ch // 4, "share": round(100 * ch / max(tot_tool, 1), 1), "median_chars": s[len(s) // 2], "p95_chars": s[int(len(s) * 0.95)], "max_chars": s[-1]})
    big_share = {}
    for th in (2000, 8000, 20000):
        k = sum(1 for x in sz_all if x > th)
        big_share[th] = {"count": k, "pct_results": round(100 * k / max(len(sz_all), 1), 1), "pct_tokens": round(100 * sum(x for x in sz_all if x > th) / max(tot_tool, 1))}
    return {
        "since": since, "generated": time.strftime("%Y-%m-%d %H:%M"), "sessions": sessions, "assistant_turns": turns,
        "billed": dict(usage), "billed_total": billed,
        "billed_shares": {k: round(100 * v / max(billed, 1), 2) for k, v in usage.items()},
        "subagents": sub,
        "context_per_turn": {"median": _q(sorted([c for c in []]) or None, 0.5)} if False else _ctx_stats(firsts, turns_per, integral),
        "fills_context_tokens": {k: v // 4 for k, v in fills.items()},
        "fills_context_share": {k: round(100 * v / max(sum(fills.values()), 1), 1) for k, v in fills.items()},
        "by_tool": by_tool,
        "read_rereads": {"reads": reads, "rereads": reread, "pct": round(100 * reread / max(reads, 1))},
        "read_tokens_by_ext": {k: v // 4 for k, v in read_ext.most_common(8)},
        "bash_by_head": [{"head": h, "n": bash_head[h], "tokens": c // 4} for h, c in bash_head_chars.most_common(20)],
        "big_results": big_share,
        "reread_multiplier": round(reread_mult_num / max(reread_mult_den, 1), 1),
        "preamble_share_pct": round(100 * sum(min(f, 120_000) * t for f, t in zip(firsts, turns_per)) / max(integral, 1)),
        "compact_whatif": {str(w): {"context_tokens": v, "pct_of_now": round(100 * v / max(integral, 1))} for w, v in whatif.items()},
        "skills_invoked": dict(skills.most_common(40)),
        "agents_spawned": dict(agents.most_common(40)),
    }


def _q(xs, q):
    return xs[min(int(len(xs) * q), len(xs) - 1)] if xs else 0


def _ctx_stats(firsts, turns_per, integral):
    return {
        "first_turn_median": _q(firsts, 0.5), "first_turn_p75": _q(firsts, 0.75),
        "turns_per_session_median": _q(turns_per, 0.5), "turns_per_session_p90": _q(turns_per, 0.9), "turns_per_session_max": turns_per[-1] if turns_per else 0,
        "context_tokens_reread_total": integral,
    }


def human_sessions() -> set | None:
    """Sessions a human typed in, for `--sessions mine`. Optional: a JSONL file with a `session` field per line,
    named by the TOKCTL_SESSIONS_FILE env var or the `sessions_file` config key. Absent -> every session counts."""
    from tokctl import store
    p = os.environ.get("TOKCTL_SESSIONS_FILE") or store.config().get("sessions_file")
    if not p or not os.path.exists(os.path.expanduser(p)):
        return None
    out = set()
    for l in open(os.path.expanduser(p), encoding="utf-8"):
        try:
            out.add(json.loads(l)["session"])
        except Exception:
            continue
    return out


def render(r: dict) -> str:
    o = []
    a = o.append
    a(f"tokctl scan  since {r['since']}  sessions={r['sessions']}  turns={r['assistant_turns']:,}  ({r['generated']})")
    a("")
    a("BILLED TOKENS (API usage fields, exact)")
    for k, v in r["billed"].items():
        a(f"  {k:30s} {v:>16,}  {r['billed_shares'][k]:5.1f}%")
    a(f"  {'TOTAL main sessions':30s} {r['billed_total']:>16,}")
    sb = r.get("subagents") or {}
    if sb:
        a(f"  {'subagent turns (deduped)':30s} {sb['billed']:>16,}  = {sb['share_of_everything']}% of everything; {sb['runs']} runs, {sb['turns']:,} turns; top 10% of runs hold {sb['top10pct_runs_share']}% of it")
        a(f"  {'GRAND TOTAL':30s} {sb['billed'] + r['billed_total']:>16,}")
    c = r["context_per_turn"]
    a(f"  fixed preamble (first-turn context) median {c['first_turn_median']:,} tok; its share of all re-read tokens: {r['preamble_share_pct']}%")
    a(f"  turns/session median {c['turns_per_session_median']}, p90 {c['turns_per_session_p90']}, max {c['turns_per_session_max']}")
    a(f"  re-read multiplier: a token inserted into context is re-read {r['reread_multiplier']}x on average before the session ends")
    a("")
    a("WHAT FILLS THE CONTEXT (chars/4 estimate, one-time insertion)")
    for k, v in sorted(r["fills_context_tokens"].items(), key=lambda kv: -kv[1]):
        a(f"  {k:26s} {v:>12,} tok  {r['fills_context_share'][k]:5.1f}%")
    a("")
    a("TOOL RESULTS BY TOOL")
    for t in r["by_tool"]:
        a(f"  {t['tool'][:34]:34s} n={t['n']:>6}  {t['tokens']:>11,} tok  {t['share']:5.1f}%  med={t['median_chars']:>6} p95={t['p95_chars']:>7} max={t['max_chars']:>7} chars")
    rr = r["read_rereads"]
    a(f"  Read re-reads of a path already read in-session: {rr['rereads']} of {rr['reads']} ({rr['pct']}%)")
    a("  Read tokens by extension: " + ", ".join(f"{k}={v:,}" for k, v in r["read_tokens_by_ext"].items()))
    a("")
    a("BASH OUTPUT BY COMMAND")
    for b in r["bash_by_head"][:14]:
        a(f"  {b['head']:34s} n={b['n']:>5}  {b['tokens']:>10,} tok")
    a("")
    a("SIZE CONCENTRATION")
    for th, v in r["big_results"].items():
        a(f"  results >{int(th)//1000}k chars: {v['count']} ({v['pct_results']}% of results) hold {v['pct_tokens']}% of tool-result tokens")
    a("")
    a("WHAT-IF: auto-compact window (modelled from the per-turn context series)")
    for w, v in r["compact_whatif"].items():
        a(f"  window {int(w):>9,}: context re-read would be {v['pct_of_now']:>3}% of today's")
    a("")
    a("SKILLS INVOKED: " + ", ".join(f"{k}×{v}" for k, v in list(r["skills_invoked"].items())[:12]))
    a("AGENTS SPAWNED: " + ", ".join(f"{k}×{v}" for k, v in list(r["agents_spawned"].items())[:12]))
    return "\n".join(o)
