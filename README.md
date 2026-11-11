# tokctl

Measure where your Claude Code tokens actually go, then cut the part that is re-read on every turn.

Built after scanning 231 real sessions (100,785 main-session turns plus 307,133 deduplicated subagent turns,
121.4 billion billed tokens in six weeks) and finding that the popular advice is aimed at the wrong number.

## What the measurement showed

| billed component (main sessions) | share |
|---|---|
| cache re-reads of the existing context | 98.4% |
| cache writes | 1.3% |
| output tokens | 0.3% |

| who spent it | share of everything |
|---|---|
| subagents (4,263 runs) | 65% |
| main sessions | 35% |

The top 10% of subagent runs held 69% of the subagent bill: long lanes of 1,000 to 3,000 turns running at the
auto-compact ceiling (about 967k tokens of context per turn). Subagent records are written twice by Claude Code
(in the parent transcript as sidechain and in the subagent file); `tokctl scan` de-duplicates by request id.

Every token inserted into the context is re-read on every later turn of the session. In the scanned sessions
that was **x58 on average** (p90 sessions ran 600+ turns at a median context of 366k tokens, p90 822k).
Consequences:

- Making the model terser (output trimming) attacks 0.3% of the bill.
- Filtering noisy logs helps only if your workload is noisy. In this one 72% of large Bash output was
  deliberate code reading (`sed -n`, `cat`, `head`) that must stay byte-exact.
- The fixed preamble (CLAUDE.md, memory, agent and skill descriptions) was 83k tokens per turn and
  **18% of all billed tokens** on its own.
- The auto-compact window is the single biggest lever: letting sessions run to 1M tokens means the late
  turns each cost 800k+ cache reads.

## What tokctl does

1. **`tokctl scan`** – exact billed tokens from the `usage` fields in your transcripts, attributed by tool,
   command, file type, re-read rate, preamble share, and a what-if model for compaction windows.
2. **Bash output governor** (PostToolUse hook) – replaces noisy results (tests, installs, logs, listings,
   searches, oversized JSON) with a compact form and keeps the full text on disk for 7 days.
   `read`-class output is never altered below its cap. Fetch anything back with `tokctl show <id> -g REGEX`.
3. **Read de-duplication** (PreToolUse hook) – refuses an identical re-read of a file that has not changed
   since it was read a few calls ago, and tells the model when it read it. Never fires across a compaction.
4. **Compaction carry-forward** (PreCompact hook) – instructions that force the summary to keep open threads,
   decisions, paths, numbers and the next action, so a smaller auto-compact window does not drop work.
5. **Auto-compact window** – opt-in: `tokctl install --compact-window 400000` sets `autoCompactWindow`.
   Off by default because compaction is the one lever that can drop detail mid-task.
6. **`tokctl preamble`** – what sits in every turn's system prompt and which levers exist.
7. **`tokctl report`** – measured tokens removed at insertion (exact), with the re-read multiplier shown
   separately and labelled as a model.
8. **Context nudges** (PostToolUse / UserPromptSubmit hooks) – when the live context is heavy and the last
   turns were single tool calls, one line states the context size and asks for batched calls or delegation;
   when a session passes 400k, one line tells the user the per-turn cost so the compaction decision is theirs.
9. **Subagent handoff** – inside a subagent whose context passes 350k, one line asks it to finish the current
   step, write a structured handoff file and return, so the orchestrator continues in a fresh context instead of
   paying the ceiling price for every remaining step. These lanes were already being auto-compacted at 967k;
   a deliberate handoff keeps more than a compaction summary does.

## Honest benchmark

`tokctl bench` replays recorded outputs through the governor. Two numbers, both reported:

| source | Bash tokens removed |
|---|---|
| synthetic noisy fixtures (pytest, npm, docker logs, ls -la, git log, JSON, grep) | 93.6% |
| 26,320 real recorded Bash results from a senior builder's sessions | 2.6% |

The second number is the one that matters. Fidelity on the real set: 18,941 read-class outputs under the cap,
0 altered. Any tool that quotes you only the first kind of number is benchmarking its fixtures.

What-if from the same sessions (modelled from each turn's real context size):

| auto-compact window | context re-read vs today |
|---|---|
| 300k | 46% |
| 400k | 54% |
| 500k | 62% |
| 600k | 71% |

## Install

```
git clone https://github.com/Johnbjoseph-cybersec/tokctl ~/tokctl
cd ~/tokctl && pip install -e .          # or: ln -s ~/tokctl/bin/tokctl ~/.local/bin/tokctl
tokctl install --mode shadow             # hooks log what they would do, change nothing
tokctl scan --since 2026-10-01           # where your tokens went
tokctl bench --local                     # replay your own transcripts; nothing leaves the machine
tokctl mode enforce
```

`--sessions mine` needs a JSONL file listing the sessions a human typed in (one `{"session": "<id>"}` per line),
named by `TOKCTL_SESSIONS_FILE` or the `sessions_file` config key; without it every session counts.

Config lives in `~/.claude/tokctl/config.json`:

| key | default | meaning |
|---|---|---|
| `mode` | `enforce` | `shadow` logs only |
| `min_chars` | 1500 | results below this are never touched |
| `caps` | `{}` | override `read` (24000) / `default` (8000) char caps |
| `read_dedupe`, `read_dedupe_window` | true, 40 | refuse identical re-reads within N tool calls |
| `ctx_nudge`, `ctx_nudge_min`, `ctx_nudge_single`, `ctx_nudge_every` | true, 200000, 3, 25 | batching nudge |
| `agent_handoff`, `agent_handoff_min`, `agent_handoff_every` | true, 350000, 15 | subagent handoff |
| `prompt_ctx`, `prompt_ctx_min`, `prompt_ctx_every` | true, 400000, 8 | per-turn cost line for the user |

Hooks load in new sessions. Kill switch: `tokctl off` (creates `~/.claude/tokctl/OFF`; every hook becomes a no-op).
Remove everything: `tokctl uninstall`. Settings are edited read-modify-write with a timestamped backup.

Requires Claude Code 2.1.288+ (`autoCompactWindow`) and PostToolUse `updatedToolOutput`, verified on 2.1.294.
Python 3.9+, no dependencies.

## Tests

```
python3 -m pytest tests
python3 tokctl/cli.py bench
```
