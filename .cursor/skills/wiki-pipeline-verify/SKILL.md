---
name: wiki-pipeline-verify
description: "Verify the wiki-pipeline-template CanonicalUrl and Placement contract with pytest. Use when a change touches URL identity, host aliases, query allowlists, percent-decoding, quarantine, or publish/fix placement. Surface is the pytest suite, not the Quartz site."
---

# Wiki pipeline contract verification

The surface under test is the typed boundary in `scripts/pipeline_contract.py`: `CanonicalUrl` and `Placement`. Quartz is a separate static site. This skill does not build or launch it. Two verification runs can execute side by side; pytest uses its own `tmp_path` directories and does not share a server, port, or vault.

## When to run

Run this after any edit to `scripts/pipeline_contract.py`, `tests/test_pipeline_contract.py`, or `pytest.ini`, and before claiming URL identity or quarantine placement still holds. Also run it when a change proposes to strip query parameters, decode percent-escapes, alias hosts, or move notes during publish/fix.

## Launch

From the repository root, with a network path that can reach PyPI:

```bash
python3 -m pip install pytest
python3 -m pytest --collect-only -q tests/test_pipeline_contract.py
```

There is no server. Ready means the collect command exits 0 and lists `tests/test_pipeline_contract.py`. `pytest.ini` sets `testpaths = tests` and `pythonpath = scripts`, so a later `python3 -m pytest` collects this file with the rest of `tests/`.

## Doctor

Run this first when a result looks wrong. Both commands are read-only.

```bash
PYTHONPATH=scripts python3 -c "from pipeline_contract import CanonicalUrl, Placement, Space; print(CanonicalUrl.parse('https://x.com/a').identity)"
python3 -m pytest --collect-only -q tests/test_pipeline_contract.py
```

The import must print `https://x.com/a`. Collect must exit 0. If import fails, the process is not using this checkout's `scripts/` directory. If collect fails, pytest is missing or `pytest.ini` was changed. Do not drive the suite until both succeed.

## Drive

The harness is pytest. Tests live in `tests/test_pipeline_contract.py`. They call `CanonicalUrl.parse`, `Publishable`, and `apply_publish_fix` on temporary directories. They do not call `scripts/pipeline.py` or `scripts/fix_paths.py`.

```bash
python3 -m pytest tests/test_pipeline_contract.py -q --tb=short
```

One feature at a time:

```bash
python3 -m pytest tests/test_pipeline_contract.py -q --tb=short -k "canonical_url or wordpress or percent_decode or box_com or legacy_normalizers"
python3 -m pytest tests/test_pipeline_contract.py -q --tb=short -k "placement or publish_fix or legacy_quarantine or conflict_markers"
```

The full configured suite, which includes the older tests plus this file:

```bash
python3 -m pytest -q --tb=line
```

## Evidence

Write stdout and stderr to a run-specific file outside the repo. The file is the proof. Keep the command, the exit code, and the pytest summary line (`passed` / `failed`).

```bash
mkdir -p /tmp/wiki-pipeline-verify/evidence
set -o pipefail
python3 -m pytest tests/test_pipeline_contract.py -q --tb=short \
  | tee /tmp/wiki-pipeline-verify/evidence/contract.txt
echo "exit:$?" | tee -a /tmp/wiki-pipeline-verify/evidence/contract.txt
```

A passing summary is not proof if the file was produced by editing an assertion to match the legacy normalizer. The legacy tests in the same file must still show the old rules disagreeing with `CanonicalUrl` and tripping `quarantine walk`.

## What a failure means

- `CanonicalUrl` identity assertion: host aliasing, the per-host query allowlist, or one-time percent-decode regressed. X `?s=` / `utm_*` came back, WordPress `?s=` was stripped, `twitter.com` no longer matches `x.com`, or `%2520` became a space.
- `box.com` or `foo.twitter.com` assertion: the host check became a substring test again (`"x.com" in host`).
- `legacy_normalizers` assertion: the golden value moved to match a historical helper. That is a weakened contract, not a fix.
- `quarantine walk`: `apply_publish_fix` or `Publishable` moved a quarantine note back, deleted a quarantine directory, or accepted `Space.QUARANTINE`. This is the ca2611bb failure.
- `conflict-marker`: `scripts/pipeline_contract.py` or `tests/test_pipeline_contract.py` contains a line that starts with `<<<<<<<`, `=======`, or `>>>>>>>`. A vault sync left an unresolved merge.

## What not to change

- Do not rewrite `scripts/pipeline.py`, `scripts/fix_paths.py`, or `scripts/normalize.py` to make these tests pass. The contract is `scripts/pipeline_contract.py`. A thin import is the only pipeline edit that belongs with this boundary, and only if a caller must prove it uses the type.
- Do not strip `s` or `t` on every host. That collapses WordPress `?s=` search URLs.
- Do not percent-decode a URL and then parse the query with `parse_qsl`. That decodes twice.
- Do not treat `"x.com" in host` as an alias. `box.com` is not X.
- Do not skip `_quarantine` with a string match inside a walker and call that the placement type. `space` is `wiki | garden | clippings | quarantine`. Publish/fix takes `Publishable`, which rejects quarantine.
- Do not delete empty quarantine directories in the publish/fix path.
- Do not edit a golden assertion, delete `test_legacy_normalizers_fail_the_canonical_contract`, or delete `test_legacy_quarantine_walk_fails_the_same_check` so the suite goes green.
- Do not merge, force-push, delete branches, or read or write secrets while verifying.

## Cleanup

No process is started. Pytest removes its own `tmp_path` directories. If a run created `/tmp/wiki-pipeline-verify/scratch`, remove that directory only:

```bash
rm -rf /tmp/wiki-pipeline-verify/scratch
test -f /tmp/wiki-pipeline-verify/evidence/contract.txt
```

The evidence file must still exist after cleanup. Do not delete `/tmp/wiki-pipeline-verify/evidence` or anything under the repository.

## Helpers

This skill does not ship a helper script. The commands above are the harness.

## Maintenance

When the contract gains a behavior, update the feature file under `features/` in the same change. `/maintain-verification-skill` is the upkeep loop for this map.
