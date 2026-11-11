#!/usr/bin/env python3
"""tokctl command line."""
from __future__ import annotations
import argparse
import collections
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tokctl import store  # noqa: E402


def cmd_show(a):
    found = store.find_spill(a.id)
    if not found:
        print(f"no spill with id {a.id} (retention {store.RETAIN_DAYS} days)")
        return 1
    text, meta = found
    if a.meta:
        print(json.dumps(meta, indent=1))
        return 0
    lines = text.split("\n")
    if a.json_path:
        try:
            data = json.loads(text)
        except Exception as e:
            print(f"not JSON: {e}")
            return 1
        for part in [p for p in re.split(r"[.\[\]]", a.json_path) if p]:
            data = data[int(part)] if isinstance(data, list) else data[part]
        print(json.dumps(data, indent=1)[: a.max_chars])
        return 0
    if a.grep:
        rx = re.compile(a.grep, re.I)
        hits = [i for i, l in enumerate(lines) if rx.search(l)]
        shown = set()
        for i in hits[: a.max_hits]:
            for j in range(max(0, i - a.context), min(len(lines), i + a.context + 1)):
                shown.add(j)
        last = -2
        out = []
        for j in sorted(shown):
            if j != last + 1:
                out.append("--")
            out.append(f"{j + 1}: {lines[j]}")
            last = j
        print("\n".join(out)[: a.max_chars])
        print(f"[{len(hits)} matching lines of {len(lines)}]")
        return 0
    if a.range:
        s, e = a.range.split("-")
        sel = lines[int(s) - 1: int(e)]
        print("\n".join(f"{int(s) + k}: {l}" for k, l in enumerate(sel))[: a.max_chars])
        return 0
    if a.tail:
        print("\n".join(lines[-a.tail:])[: a.max_chars])
        return 0
    print("\n".join(lines[: a.head])[: a.max_chars])
    if len(lines) > a.head:
        print(f"[{len(lines) - a.head} more lines; use -r A-B, -g REGEX, --tail N]")
    return 0


def cmd_status(a):
    from tokctl import install
    st = install.status()
    cfg = store.config()
    print(f"tokctl {'OFF (kill switch present)' if not store.enabled() else 'on'}  mode={cfg.get('mode')}  state={store.STATE}")
    for k, v in st.items():
        print(f"  {k:16s} {v}")
    led = store.read_ledger()
    today = time.strftime("%Y-%m-%d")
    t = [e for e in led if time.strftime("%Y-%m-%d", time.localtime(e["ts"])) == today]
    saved = sum(e.get("in", 0) - e.get("out", 0) for e in t)
    print(f"  today: {len(t)} governed results, {saved // 4:,} tokens removed at insertion, {sum(1 for e in t if e.get('tool') == 'Read')} re-reads refused")
    return 0


def cmd_report(a):
    led = store.read_ledger()
    if not led:
        print("ledger empty")
        return 0
    by_day = collections.defaultdict(lambda: collections.Counter())
    by_class = collections.Counter()
    by_class_in = collections.Counter()
    for e in led:
        day = time.strftime("%Y-%m-%d", time.localtime(e["ts"]))
        c = by_day[day]
        c["n"] += 1
        c["in"] += e.get("in", 0)
        c["out"] += e.get("out", 0)
        if e.get("tool") == "Read":
            c["reads_refused"] += 1
        if e.get("tool") == "Bash":
            by_class[e.get("class")] += e.get("in", 0) - e.get("out", 0)
            by_class_in[e.get("class")] += e.get("in", 0)
    mult = a.multiplier
    print(f"{'day':10s} {'results':>7s} {'in tok':>10s} {'out tok':>10s} {'removed':>9s} {'re-reads refused':>16s}")
    tot_removed = 0
    for day in sorted(by_day):
        c = by_day[day]
        rem = (c["in"] - c["out"]) // 4
        tot_removed += rem
        print(f"{day:10s} {c['n']:>7} {c['in'] // 4:>10,} {c['out'] // 4:>10,} {rem:>9,} {c['reads_refused']:>16}")
    print(f"\nmeasured: {tot_removed:,} tokens removed at insertion (exact chars/4 of what the hook cut)")
    if mult:
        print(f"modelled: x{mult} re-read multiplier (from `tokctl scan`) -> about {int(tot_removed * mult):,} billed tokens avoided. This line is a model, not a measurement.")
    print("\nby class (removed / seen):")
    for k, v in by_class.most_common():
        print(f"  {k:10s} {v // 4:>10,} / {by_class_in[k] // 4:>10,} tok  ({100 * v / max(by_class_in[k], 1):.0f}% removed)")
    return 0


def cmd_scan(a):
    from tokctl import scan
    only = scan.human_sessions() if a.sessions == "mine" else None
    r = scan.scan(a.since, only)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print(scan.render(r))
    if a.save:
        os.makedirs(os.path.join(store.STATE, "scans"), exist_ok=True)
        p = os.path.join(store.STATE, "scans", time.strftime("%Y%m%d-%H%M") + ".json")
        json.dump(r, open(p, "w"), indent=1)
        print(f"\nsaved {p}")
    return 0


def cmd_mode(a):
    cfg = store.config()
    if a.mode:
        cfg["mode"] = a.mode
        store.save_config(cfg)
    print(f"mode={cfg['mode']}")
    return 0


def cmd_off(a):
    store.ensure()
    open(store.OFF, "w").write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
    print("tokctl OFF: every hook is now a no-op (kill switch file present)")
    return 0


def cmd_on(a):
    if os.path.exists(store.OFF):
        os.remove(store.OFF)
    print("tokctl on")
    return 0


def cmd_install(a):
    from tokctl import install
    store.ensure()
    if not os.path.exists(store.CONFIG):
        store.save_config(dict(store.DEFAULTS, mode=a.mode))
    for line in install.install(a.compact_window or None):
        print(line)
    print("hooks take effect in NEW sessions (Claude Code loads hook settings at start-up)")
    return 0


def cmd_uninstall(a):
    from tokctl import install
    for line in install.uninstall():
        print(line)
    return 0


def cmd_preamble(a):
    """Fixed per-turn cost: what is in the system prompt of every turn."""
    import glob
    home = os.path.expanduser("~")

    def desc_tokens(paths):
        tot = 0
        for f in paths:
            s = open(f, errors="ignore").read()
            m = re.search(r"^description:\s*(.*?)(?=^\w[\w-]*:|^---)", s, re.S | re.M)
            tot += len((m.group(1) if m else "")[:1536]) + len(os.path.basename(f)) + 20
        return tot // 4
    rows = []
    for name in ("CLAUDE.md",):
        p = os.path.join(home, ".claude", name)
        if os.path.exists(p):
            rows.append((name, os.path.getsize(p) // 4, ""))
    mem = glob.glob(os.path.join(home, ".claude", "projects", "*", "memory", "MEMORY.md"))
    for p in mem:
        sz = os.path.getsize(p)
        rows.append(("MEMORY.md " + p.split("/projects/")[1].split("/")[0][:30], min(sz, 24_400) // 4, "OVER the 24.4KB load limit; tail lines are cut" if sz > 24_400 else ""))
    ua = glob.glob(os.path.join(home, ".claude", "agents", "*.md"))
    groups = collections.Counter()
    for f in ua:
        groups[os.path.basename(f).split("-")[0]] += 1
    rows.append((f"user agents ({len(ua)})", desc_tokens(ua), "descriptions sit in every turn; squads: " + ", ".join(f"{g}×{n}" for g, n in groups.most_common(8))))
    pa = glob.glob(os.path.join(home, ".claude", "plugins", "cache", "*", "*", "*", "agents", "*.md"))
    rows.append((f"plugin agents ({len(pa)})", desc_tokens(pa), ""))
    usk = glob.glob(os.path.join(home, ".claude", "skills", "*", "SKILL.md"))
    psk = glob.glob(os.path.join(home, ".claude", "plugins", "cache", "*", "*", "*", "skills", "*", "SKILL.md"))
    rows.append((f"user skills ({len(usk)})", desc_tokens(usk), "names + descriptions (each capped at 1,536 chars)"))
    rows.append((f"plugin skills ({len(psk)})", sum(len(os.path.basename(os.path.dirname(f))) + 8 for f in psk) // 4, "listed by name only"))
    print("FIXED PREAMBLE COMPONENTS (estimates, chars/4; the harness's own prompt and tool schemas are on top)")
    tot = 0
    for name, tok, note in rows:
        tot += tok
        print(f"  {name:44s} ~{tok:>7,} tok  {note}")
    print(f"  {'sum of the above':44s} ~{tot:>7,} tok per turn")
    print("\nLevers: move squad agents to the project dirs that use them (.claude/agents/), deny unused agents per project (permissions.deny Agent(name)), skillOverrides name-only/off for unused skills, keep MEMORY.md under its limit.")
    return 0


def cmd_bench(a):
    from tokctl import bench
    return bench.run(a)


def main(argv=None):
    p = argparse.ArgumentParser(prog="tokctl", description="measure and cut Claude Code context cost")
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("show", help="fetch a spilled full output by id")
    s.add_argument("id")
    s.add_argument("-g", "--grep")
    s.add_argument("-C", "--context", type=int, default=2)
    s.add_argument("-r", "--range", help="A-B line range")
    s.add_argument("-j", "--json-path", help="dotted path into a JSON output")
    s.add_argument("--head", type=int, default=120)
    s.add_argument("--tail", type=int)
    s.add_argument("--max-hits", type=int, default=60)
    s.add_argument("--max-chars", type=int, default=12000)
    s.add_argument("--meta", action="store_true")
    s.set_defaults(fn=cmd_show)
    s = sp.add_parser("status"); s.set_defaults(fn=cmd_status)
    s = sp.add_parser("report", help="measured savings from the ledger")
    s.add_argument("--multiplier", type=float, default=0, help="re-read multiplier from `tokctl scan` to model billed savings")
    s.set_defaults(fn=cmd_report)
    s = sp.add_parser("scan", help="where tokens went, from transcript usage fields")
    s.add_argument("--since", default=time.strftime("%Y-%m-01"))
    s.add_argument("--sessions", choices=["mine", "all"], default="all")
    s.add_argument("--json", action="store_true")
    s.add_argument("--save", action="store_true")
    s.set_defaults(fn=cmd_scan)
    s = sp.add_parser("mode"); s.add_argument("mode", nargs="?", choices=["shadow", "enforce"]); s.set_defaults(fn=cmd_mode)
    s = sp.add_parser("off", help="kill switch"); s.set_defaults(fn=cmd_off)
    s = sp.add_parser("on"); s.set_defaults(fn=cmd_on)
    s = sp.add_parser("install")
    s.add_argument("--mode", choices=["shadow", "enforce"], default="shadow")
    s.add_argument("--compact-window", type=int, default=0, help="opt-in: compact earlier (lossy for continuity); 0 = leave default")
    s.add_argument("--no-compact-window", action="store_true")
    s.set_defaults(fn=cmd_install)
    s = sp.add_parser("uninstall"); s.set_defaults(fn=cmd_uninstall)
    s = sp.add_parser("preamble", help="fixed per-turn system prompt cost"); s.set_defaults(fn=cmd_preamble)
    s = sp.add_parser("bench", help="replay recorded outputs through the governor")
    s.add_argument("--local", action="store_true", help="use your own transcripts (private)")
    s.add_argument("--since", default="2026-10-01")
    s.add_argument("--sessions", choices=["mine", "all"], default="mine")
    s.add_argument("--limit", type=int, default=0)
    s.set_defaults(fn=cmd_bench)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
