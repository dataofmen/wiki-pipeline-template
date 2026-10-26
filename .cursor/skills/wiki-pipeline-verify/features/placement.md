# Placement

Placement records a note as `wiki`, `garden`, `clippings`, or `quarantine`. The publish/fix path can move a wiki note into `wiki/concepts`. It cannot move a quarantine note back or delete a quarantine directory.

## Sub-features

- `placement-space` reads `_quarantine` once and stores `Space.QUARANTINE`.
- `placement-publishable` rejects quarantine when building a `Publishable`.
- `placement-fix` moves a misplaced wiki note and leaves quarantine notes and empty quarantine directories in place.
- `placement-legacy-walk` shows the pre-ca2611b walker failing the `quarantine walk` assertion and deleting the empty quarantine directory.
- `placement-conflict-marker` fails when a contract source line starts with a merge marker.

## How to get to it (user POV)

- Run the placement tests from the repository root.
- There is no browser control. The observable state is files under pytest's temporary directory, reported as pass or fail.

## Driving it with pytest

Preconditions:

- Doctor commands in `SKILL.md` exited 0.
- `tests/test_pipeline_contract.py` is collected.
- The process can create temporary directories. No seeded vault is required.

- **Run the feature.** From the repository root, run `python3 -m pytest tests/test_pipeline_contract.py -q --tb=short -k "placement or publish_fix or legacy_quarantine or conflict_markers"`. Exit code is 0.
- **Confirm quarantine stays.** `test_publish_fix_does_not_move_quarantine_notes` passes. On failure the message contains `quarantine walk`.
- **Confirm the old walker fails that check.** `test_legacy_quarantine_walk_fails_the_same_check` passes. The inner assertion raises `AssertionError` matching `quarantine walk`, and the note is found under `wiki/concepts/`.
- **Confirm merge markers.** `test_no_conflict_markers_in_contract_sources` passes. On failure the message starts with `conflict-marker:`.
- **Proof.** Run `set -o pipefail; python3 -m pytest tests/test_pipeline_contract.py -q --tb=short -k "placement or publish_fix or legacy_quarantine or conflict_markers" | tee /tmp/wiki-pipeline-verify/evidence/placement.txt; echo "exit:$?" | tee -a /tmp/wiki-pipeline-verify/evidence/placement.txt`. The file contains the pytest summary and `exit:0`. `set -o pipefail` is required; without it `tee` hides a failing pytest.

## Gotchas

- `scripts/fix_paths.py` on this branch already skips `_quarantine` by string match. That skip is not `Placement`. Do not report a run of `fix_paths.py` as this feature.
- A publish/fix that returns without moving anything still fails if `wiki/events/loose.md` is still in `events/`. The typed path must relocate that publishable note.
- Deleting an empty `wiki/_quarantine/unused` directory fails the test with `quarantine walk`.
- The conflict-marker scan covers `scripts/pipeline_contract.py` and `tests/test_pipeline_contract.py` only. Markers in other files are outside this check.
- Do not remove `test_legacy_quarantine_walk_fails_the_same_check` to silence a failure. That test is how the old walk is shown to trip the message.
