"""Output governor for Bash tool results.

Design rule, measured on 231 real sessions: 72% of large Bash output is deliberate
code reading (sed -n / cat / head). That class is NEVER altered byte-for-byte below
its cap, because the model may paste it into an exact-match Edit. Only noise classes
(tests, builds, logs, listings, searches, generic) are compacted, and the full text
is always kept on disk so nothing is lost.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
SPINNER = re.compile(r"^\s*[\u2800-\u28ff|/\\-]\s")

CAPS = {"read": 24000, "default": 8000}
MIN_CHARS = 1500          # below this nothing is touched; the result is already cheap

# --- command classification -------------------------------------------------
_READ = re.compile(r"(?:^|[;&|(]\s*|\n\s*)(?:cat|sed\s+-n|sed\s+'|sed\s+\"|head|tail\s+-n|tail\s+-\d|bat|cut\s+-c|nl|more|less)\b")
_TESTS = re.compile(r"\b(pytest|py\.test|npm\s+(run\s+)?test|npx\s+jest|jest|vitest|go\s+test|cargo\s+test|phpunit|unittest|mocha|playwright\s+test|rspec)\b")
_BUILD = re.compile(r"\b(npm\s+(ci|install|i|run\s+build)|pnpm\s+(install|i|build)|yarn(\s+install|\s+build)?|pip3?\s+install|poetry\s+install|uv\s+(pip\s+)?(install|sync)|cargo\s+build|go\s+build|docker\s+(compose\s+)?build|tsc|next\s+build|vite\s+build|webpack|make)\b")
_LOGS = re.compile(r"\b(docker\s+(compose\s+)?logs|docker-compose\s+logs|journalctl|log\s+show|launchctl|\.log\b)")
_GIT = re.compile(r"(?:^|[;&|]\s*)git\s+(log|diff|show|status|blame)\b")
_LIST = re.compile(r"(?:^|[;&|]\s*)(ls|tree|du|find|lsof|ps)\b")
_SEARCH = re.compile(r"(?:^|[;&|]\s*)(grep|rg|ag|ack)\b")


def classify(command: str) -> str:
    c = command or ""
    if _READ.search(c):
        return "read"
    if _TESTS.search(c):
        return "tests"
    if _BUILD.search(c):
        return "build"
    if _LOGS.search(c):
        return "logs"
    if _GIT.search(c):
        return "git"
    if _LIST.search(c):
        return "listing"
    if _SEARCH.search(c):
        return "search"
    return "generic"


@dataclass
class Result:
    text: str
    klass: str
    in_chars: int
    out_chars: int
    transforms: list = field(default_factory=list)
    truncated: bool = False

    @property
    def changed(self) -> bool:
        return self.text != self._orig if hasattr(self, "_orig") else self.in_chars != self.out_chars


# --- generic transforms -----------------------------------------------------
def _strip_ansi(s: str) -> str:
    return ANSI.sub("", s)


def _progress_lines(s: str) -> str:
    # A carriage-return progress bar leaves many overwritten segments; keep the last.
    if "\r" not in s:
        return s
    out = []
    for line in s.split("\n"):
        if "\r" in line:
            line = line.split("\r")[-1]
        out.append(line)
    return "\n".join(out)


def _collapse_blank(lines: list[str]) -> list[str]:
    out, blank = [], 0
    for l in lines:
        if l.strip():
            blank = 0
            out.append(l)
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return out


def _dedupe(lines: list[str], key=lambda l: l) -> tuple[list[str], int]:
    out, removed = [], 0
    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and key(lines[j]) == key(lines[i]) and lines[i].strip():
            j += 1
        out.append(lines[i])
        if j - i > 1:
            out.append(f"  [tokctl: previous line repeated {j - i - 1} more times]")
            removed += j - i - 1
        i = j
    return out, removed


_TS = re.compile(r"^\s*(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d[.,\d]*Z?|\[?\d\d:\d\d:\d\d[.,\d]*\]?|\w{3}\s+\d+\s+\d\d:\d\d:\d\d)\s*")


def _log_key(l: str) -> str:
    return _TS.sub("", l)


# --- class transforms -------------------------------------------------------
_PY_PASS = re.compile(r"^\S+\.py(::\S+)?\s+PASSED(\s+\[\s*\d+%\])?\s*$")
_PY_DOTS = re.compile(r"^\S+\.py\s+[.sxX]+\s*(\[\s*\d+%\])?\s*$")
_JS_PASS = re.compile(r"^\s*(✓|√|✔|PASS\b)")
_GO_PASS = re.compile(r"^(=== RUN|--- PASS|\s*ok\s+\S+\s+[\d.]+s|PASS)$|^(=== RUN|--- PASS)")


def _tests(lines: list[str]) -> tuple[list[str], list[str]]:
    out, dropped = [], 0
    for l in lines:
        if _PY_PASS.match(l) or _PY_DOTS.match(l) or _JS_PASS.match(l) or _GO_PASS.match(l):
            dropped += 1
            continue
        out.append(l)
    notes = [f"{dropped} passing-test lines removed"] if dropped else []
    return out, notes


_BUILD_NOISE = re.compile(
    r"^\s*(\[\d+/\d+\]|npm\s+(notice|http|timing)|Progress:|Downloading|Collecting\s|Requirement already satisfied|Using cached|Preparing metadata|Resolved \d+|Fetching|Compiling\s|Checking\s|Downloaded\s|added \d+ packages? in|\s*(found|audited) \d+ packages)",
)
_BUILD_KEEP = re.compile(r"(error|warn|fail|exception|traceback|success|done|built|finished|completed|✓|✗)", re.I)


def _build(lines: list[str]) -> tuple[list[str], list[str]]:
    out, dropped = [], 0
    for l in lines:
        if (_BUILD_NOISE.match(l) or SPINNER.match(l)) and not _BUILD_KEEP.search(l):
            dropped += 1
            continue
        out.append(l)
    return out, ([f"{dropped} install/build progress lines removed"] if dropped else [])


_LOG_IMPORTANT = re.compile(r"\b(error|fatal|panic|traceback|exception|critical|warn)\b", re.I)


def _logs(lines: list[str]) -> tuple[list[str], list[str]]:
    lines, dup = _dedupe(lines, key=_log_key)
    notes = [f"{dup} repeated log lines folded"] if dup else []
    if len(lines) > 180:
        head, tail = lines[:20], lines[-120:]
        mid = [l for l in lines[20:-120] if _LOG_IMPORTANT.search(l)][:30]
        skipped = len(lines) - 140
        lines = head + [f"  [tokctl: {skipped} middle lines skipped; {len(mid)} error/warn lines kept below]"] + mid + ["  [tokctl: ... tail follows]"] + tail
        notes.append(f"log tail kept, {skipped} middle lines skipped")
    return lines, notes


_LS_LONG = re.compile(r"^([dl\-bcps])[rwxsStT\-]{9}[@+]?\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\w{3}\s+\d+\s+[\d:]{4,5})\s+(.*)$")


def _listing(lines: list[str]) -> tuple[list[str], list[str]]:
    out, n = [], 0
    for l in lines:
        m = _LS_LONG.match(l)
        if m:
            t, size, date, name = m.groups()
            out.append(f"{t} {int(size):>9} {date} {name}")
            n += 1
        elif l.startswith("total ") and l[6:].strip().isdigit():
            continue
        else:
            out.append(l)
    return out, ([f"{n} ls -l rows compacted (type size date name)"] if n else [])


def _git(lines: list[str]) -> tuple[list[str], list[str]]:
    # Full-format `git log` -> one line per commit. Diffs and status pass through.
    if not any(l.startswith("commit ") for l in lines[:3]):
        return lines, []
    commits, cur = [], None
    for l in lines:
        if l.startswith("commit "):
            cur = {"h": l.split()[1][:9], "d": "", "m": ""}
            commits.append(cur)
        elif cur is None:
            continue
        elif l.startswith("Date:"):
            cur["d"] = l[5:].strip()[:16]
        elif l.startswith("    ") and not cur["m"]:
            cur["m"] = l.strip()
    out = [f"{c['h']} {c['d']}: {c['m']}" for c in commits]
    return out, ["git log compacted to one line per commit"]


def _search(lines: list[str]) -> tuple[list[str], list[str]]:
    out, cut = [], 0
    for l in lines:
        if len(l) > 600:
            out.append(l[:600] + f" …[+{len(l) - 600} chars]")
            cut += 1
        else:
            out.append(l)
    return out, ([f"{cut} over-long match lines shortened"] if cut else [])


def _json_summary(s: str) -> str | None:
    try:
        data = json.loads(s)
    except Exception:
        return None

    def shape(v, depth=0):
        if isinstance(v, dict):
            if depth >= 2:
                return f"{{…{len(v)} keys}}"
            inner = ", ".join(f"{k}: {shape(x, depth + 1)}" for k, x in list(v.items())[:25])
            more = f", …+{len(v) - 25} keys" if len(v) > 25 else ""
            return "{" + inner + more + "}"
        if isinstance(v, list):
            if not v:
                return "[]"
            return f"[{len(v)} × {shape(v[0], depth + 1)}]"
        r = json.dumps(v)
        return r if len(r) <= 60 else r[:57] + "…\""
    return "[tokctl: JSON structure; values via `tokctl show ID -j KEY.PATH`]\n" + shape(data)


# --- main entry -------------------------------------------------------------
def govern(command: str, stdout: str, caps: dict | None = None) -> Result:
    caps = {**CAPS, **(caps or {})}
    klass = classify(command)
    orig = stdout or ""
    n_in = len(orig)
    res = Result(text=orig, klass=klass, in_chars=n_in, out_chars=n_in)
    if n_in < MIN_CHARS:
        return res

    if klass == "read":
        # Lossless. Only cap with head/tail; the model can fetch the rest from the spill.
        cap = caps["read"]
        if n_in > cap:
            res.text = _cap(orig, cap)
            res.truncated = True
            res.transforms.append("capped (lossless above the cut is on disk)")
        res.out_chars = len(res.text)
        return res

    s = _progress_lines(_strip_ansi(orig))
    lines = [l.rstrip() for l in s.split("\n")]
    lines = _collapse_blank(lines)
    notes: list[str] = []
    if klass == "tests":
        lines, notes = _tests(lines)
    elif klass == "build":
        lines, notes = _build(lines)
    elif klass == "logs":
        lines, notes = _logs(lines)
    elif klass == "listing":
        lines, notes = _listing(lines)
    elif klass == "git":
        lines, notes = _git(lines)
    elif klass == "search":
        lines, notes = _search(lines)
    if klass not in ("logs", "git"):
        lines, dup = _dedupe(lines)
        if dup:
            notes.append(f"{dup} duplicate lines folded")
    text = "\n".join(lines)
    cap = caps["default"]
    if len(text) > cap:
        js = _json_summary(orig) if klass in ("generic", "search", "logs") else None
        if js and len(js) < cap:
            text = js
            notes.append("large JSON replaced by its structure")
        else:
            text = _cap(text, cap)
            notes.append("capped")
        res.truncated = True
    res.text = text
    res.out_chars = len(text)
    res.transforms = notes
    return res


def _cap(s: str, cap: int) -> str:
    if len(s) <= cap:
        return s
    head_n = int(cap * 0.65)
    tail_n = cap - head_n
    head = s[:head_n]
    tail = s[-tail_n:]
    # cut on line boundaries where possible
    hi = head.rfind("\n")
    ti = tail.find("\n")
    if hi > head_n * 0.6:
        head = head[:hi]
    if 0 <= ti < tail_n * 0.4:
        tail = tail[ti + 1:]
    hidden_lines = s.count("\n") - head.count("\n") - tail.count("\n")
    return head + f"\n  [tokctl: {max(hidden_lines, 0)} lines / {len(s) - len(head) - len(tail):,} chars hidden here]\n" + tail


def footer(res: Result, spill_id: str | None) -> str:
    if res.in_chars == res.out_chars and not res.transforms:
        return ""
    saved = res.in_chars - res.out_chars
    parts = [f"[tokctl: {res.klass}; {res.in_chars:,} → {res.out_chars:,} chars"]
    if res.transforms:
        parts.append("; " + "; ".join(res.transforms))
    if spill_id:
        parts.append(f". Full output: `tokctl show {spill_id}` (-g REGEX | -r A-B | -j PATH)")
    parts.append("]")
    return "".join(parts) if saved > 0 or res.transforms else ""
