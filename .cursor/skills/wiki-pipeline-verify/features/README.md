# Wiki pipeline contract verification map

This directory is the maintained source for verifying `CanonicalUrl` and `Placement` in wiki-pipeline-template. Read the index before driving the suite, then use the matching feature file as the recipe.

## Baseline preconditions

- Working directory is the repository root.
- `python3 -m pip install pytest` has completed in this environment.
- `pytest.ini` sets `testpaths = tests` and `pythonpath = scripts`.
- `PYTHONPATH=scripts python3 -c "from pipeline_contract import CanonicalUrl"` prints nothing and exits 0.
- `python3 -m pytest --collect-only -q tests/test_pipeline_contract.py` exits 0.
- Proof files go to `/tmp/wiki-pipeline-verify/evidence/`. Scratch goes to `/tmp/wiki-pipeline-verify/scratch/` and is the only path cleanup may delete.

## Driving conventions

- Run pytest from the repository root. Do not `cd` into `scripts/` or `tests/`.
- Treat every command as literal. Keep the `-k` expressions unchanged.
- Drive `CanonicalUrl` and `Placement` through `tests/test_pipeline_contract.py`. Do not import `scripts/pipeline.py` as a stand-in.
- A green run that required editing a golden assertion is not a pass.
- Leave files in `/tmp/wiki-pipeline-verify/evidence/` in place.

## Proof and skip reporting

- Capture the pytest command, stdout, stderr, and exit code in the evidence file.
- A skipped `-k` selection is not verified through the other selection. Record the command and the unmet precondition.
- The legacy cases must fail their historical rules inside the same file. A report that only quotes the new-type assertions is incomplete.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with pytest` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

## Features

- [Canonical URL](./canonical-url.md) covers host aliases, the per-host query allowlist, one-time percent-decode, and the legacy normalizers that fail those rules.
- [Placement](./placement.md) covers the four spaces, the publish/fix refusal to move quarantine notes, and the conflict-marker check.
