# Contributing

- Every claim of savings must come from `tokctl bench --local` on real transcripts or `tokctl scan`; fixture numbers alone are not accepted in a PR description.
- A change to the governor must keep `read`-class output byte-identical under its cap. `tokctl bench` fails on any altered read.
- Hooks must never fail a tool call: wrap everything, exit 0, log to `hook-errors.log`.
- Add a test in `tests/` for every rule you add or change. `python -m pytest -q tests` must pass on 3.9 and 3.12.
