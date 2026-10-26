# Snapshots and properties

The sample report prints FetchOutcome, Placement, and NoteRecord as a text table. A quarantine note that publish/fix moves back, or a percent-escape that is decoded twice, changes that table. Hypothesis checks that `normalize` is idempotent, that `x.com` and `twitter.com` aliases share one identity, and that percent-escapes are decoded once.

## Sub-features

- `snapshot-report` commits the sample table in `tests/test_pipeline_snapshots.py`.
- `snapshot-quarantine` keeps quarantine notes and `wiki/_quarantine/unused` in place, and moves `wiki/events/loose.md` to `wiki/concepts/loose.md`.
- `property-idempotent` checks `normalize(normalize(x)) == normalize(x)`, including `http://x.com//` and `%2520`.
- `property-alias` checks `x.com` and `twitter.com` aliases, and rejects hosts that only contain those names.
- `property-decode-once` checks a double-encoded path and query stay at one decode.

## How to get to it (user POV)

- Run the snapshot and property tests from the repository root.
- There is no browser control and no HTTP server. The observable result is the inline snapshot and the pytest summary.

## Driving it with pytest

Preconditions:

- Doctor commands in `SKILL.md` exited 0.
- `python3 -m pip install -r requirements-dev.txt` has completed.
- `tests/test_pipeline_snapshots.py` and `tests/test_pipeline_properties.py` are collected.

- **Run the feature.** From the repository root, run `python3 -m pytest tests/test_pipeline_snapshots.py tests/test_pipeline_properties.py -q --tb=short`. Exit code is 0.
- **Confirm the report.** `test_sample_pipeline_report` passes. `Kept` and `Personal` have `publishable=no` and `disk_after` equal to `input`. `Moves` lists `wiki/events/loose.md` and `garden/drafts/draft.md` only. `wiki/_quarantine/unused` is `present`.
- **Confirm idempotence.** `test_normalize_is_idempotent` passes. A failure names the smallest URL whose second `normalize` changed.
- **Confirm aliases.** `test_x_and_twitter_aliases_canonicalize_to_the_same_value` passes, and `test_host_containing_x_com_is_not_an_alias` passes. A failure on the second means `box.com` was treated as X.
- **Confirm one decode.** `test_percent_encoding_is_decoded_once` passes. A failure on `%2520` means the value became a space.
- **Proof.** Run `mkdir -p /tmp/wiki-pipeline-verify/evidence` and `set -o pipefail; python3 -m pytest tests/test_pipeline_snapshots.py tests/test_pipeline_properties.py -q --tb=short | tee /tmp/wiki-pipeline-verify/evidence/snapshots-and-properties.txt; echo "exit:$?" | tee -a /tmp/wiki-pipeline-verify/evidence/snapshots-and-properties.txt`. The file contains the pytest summary and `exit:0`. `set -o pipefail` is required; without it `tee` hides a failing pytest.

## Gotchas

- The snapshot is the assertion. Editing the table to match a bad `disk_after` is not a fix.
- `pytest --inline-snapshot=fix` rewrites the table. `pyproject.toml` sets `format-command = "cat"` so the rewrite does not need Black. Review the diff before keeping it.
- `scripts/pipeline.py` `_normalize_url` still unquotes before `parse_qsl` and does not alias hosts. These tests call `normalize` in `scripts/pipeline_contract.py`.
- A green property run that only compared `x.com` with `x.com` would miss a broken alias. The strategy swaps hosts inside the alias set.
- Do not point this run at `tests/test_dedup.py` and report it as the sample report.
