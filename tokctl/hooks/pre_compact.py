#!/usr/bin/env python3
"""PreCompact: instructions for the compaction summary so a smaller context window does not drop threads.

Stdout (exit 0) becomes custom instructions for the summary. Kept well under the 10,000-char cap.
"""
import sys

INSTRUCTIONS = """Compaction rules (tokctl). The summary must let work continue with zero re-discovery:
1. USER INTENT: the user's current goal in their own words, plus every explicit instruction, constraint, number, name and deadline they gave. Quote the user's most recent message verbatim.
2. STATE: what is DONE and VERIFIED (how it was verified), what is IN FLIGHT (exact step), what is NOT STARTED. Counts, not adjectives.
3. OPEN THREADS: every question asked and not yet answered, every side request not yet delivered, every error seen and not yet fixed. Dropping one of these is the failure this list exists to prevent.
4. DECISIONS: each decision taken, the alternative rejected, and why. Anything the user approved or rejected.
5. ARTIFACTS: every file path created or edited (absolute), every URL, every command that must be re-run, every `tokctl show <id>` reference still needed.
6. NUMBERS: all measured figures with their units and where they came from. Never round away a figure the user may ask about.
7. NEXT ACTION: the exact next tool call or step, so the next turn starts working rather than orienting.
Write it as dense labelled sections, not prose. Prefer file paths and identifiers over descriptions."""


def main() -> int:
    try:
        sys.stdin.read()
    except Exception:
        pass
    print(INSTRUCTIONS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
