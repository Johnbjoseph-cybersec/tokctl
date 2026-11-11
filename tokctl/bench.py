"""Replay recorded Bash results through the governor and report honest per-class numbers.

Two sources:
  fixtures/  synthetic, public-safe outputs (pytest, npm, docker logs, ls -la, git log, JSON, code read)
  --local    your own Claude Code transcripts (never leaves the machine)
Fidelity check: every `read`-class output at or under its cap must come back byte-identical.
"""
from __future__ import annotations
import collections
import glob
import json
import os

from tokctl import filter as flt

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench", "fixtures")


def _iter_local(since: str, only_mine: bool, limit: int):
    from tokctl import scan
    only = scan.human_sessions() if only_mine else None
    n = 0
    for f in scan.session_files(only):
        idmap = {}
        for line in open(f, errors="ignore"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("isSidechain") or d.get("timestamp", "")[:10] < since:
                continue
            m = d.get("message") or {}
            if d.get("type") == "assistant":
                for b in m.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                        idmap[b["id"]] = (b.get("input") or {}).get("command", "")
            elif d.get("type") == "user" and isinstance(m.get("content"), list):
                for b in m["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in idmap:
                        c = b.get("content")
                        s = c if isinstance(c, str) else "".join(x.get("text", "") for x in c if isinstance(x, dict))
                        yield idmap[b["tool_use_id"]], s
                        n += 1
                        if limit and n >= limit:
                            return


def _iter_fixtures():
    for p in sorted(glob.glob(os.path.join(FIX, "*.cmd"))):
        cmd = open(p).read().strip()
        out = open(p[:-4] + ".out", errors="replace").read()
        yield cmd, out


def run(a) -> int:
    src = _iter_local(a.since, a.sessions == "mine", a.limit) if a.local else _iter_fixtures()
    by = collections.defaultdict(lambda: {"n": 0, "in": 0, "out": 0, "touched": 0})
    fid_checked = fid_bad = 0
    for cmd, out in src:
        r = flt.govern(cmd, out)
        b = by[r.klass]
        b["n"] += 1
        b["in"] += r.in_chars
        b["out"] += r.out_chars
        if r.out_chars != r.in_chars or r.transforms:
            b["touched"] += 1
        if r.klass == "read" and r.in_chars <= flt.CAPS["read"]:
            fid_checked += 1
            if r.text != out:
                fid_bad += 1
    print(f"{'class':10s} {'results':>8s} {'in tok':>12s} {'out tok':>12s} {'removed':>8s} {'touched':>8s}")
    ti = to = 0
    for k, b in sorted(by.items(), key=lambda kv: -kv[1]["in"]):
        ti += b["in"]
        to += b["out"]
        print(f"{k:10s} {b['n']:>8} {b['in'] // 4:>12,} {b['out'] // 4:>12,} {100 * (b['in'] - b['out']) / max(b['in'], 1):>7.1f}% {b['touched']:>8}")
    print(f"{'ALL':10s} {sum(b['n'] for b in by.values()):>8} {ti // 4:>12,} {to // 4:>12,} {100 * (ti - to) / max(ti, 1):>7.1f}%")
    print(f"fidelity: {fid_checked} read-class outputs under the cap checked, {fid_bad} altered (must be 0)")
    return 1 if fid_bad else 0
