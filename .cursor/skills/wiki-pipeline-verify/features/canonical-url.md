# Canonical URL

Canonical URL gives one identity to a source link: `twitter.com` and `x.com` match, X share parameters and `utm_*` drop, WordPress `?s=` stays, and percent-escapes are decoded once.

## Sub-features

- `canonical-quotes` strips wrapping quotes, `utm_*`, and fragments, and keeps a non-tracking query key.
- `canonical-x-alias` collapses `twitter.com`, `mobile.twitter.com`, and `x.com`, and drops `s`, `t`, `utm_*`, and any other X query key.
- `canonical-wordpress` keeps `?s=` on a non-X host and still drops `utm_*` and `si`.
- `canonical-decode-once` decodes a LinkedIn path once and leaves a `%2520` query value as `%20`.
- `canonical-not-substring` does not treat `box.com` or `foo.twitter.com` as X.
- `canonical-legacy` shows the pre-type normalizers failing those identities.

## How to get to it (user POV)

- Run the CanonicalUrl tests from the repository root.
- There is no browser control and no HTTP server for this feature.

## Driving it with pytest

Preconditions:

- Doctor commands in `SKILL.md` exited 0.
- `tests/test_pipeline_contract.py` is collected.

- **Run the feature.** From the repository root, run `python3 -m pytest tests/test_pipeline_contract.py -q --tb=short -k "canonical_url or wordpress or percent_decode or box_com or legacy_normalizers"`. Exit code is 0 and the summary counts only passed tests.
- **Confirm X identity.** The `test_x_share_params_collapse_across_host_aliases` line is in the passed set. A failure here means `?s=` survived or `twitter.com` did not alias to `x.com`.
- **Confirm WordPress.** The `test_wordpress_search_is_not_stripped` line is in the passed set. A failure here means `?s=zettelkasten` was removed.
- **Confirm one decode.** The `test_percent_decode_once_for_linkedin_path_and_query` line is in the passed set. A failure on `%2520` means the value became a space.
- **Confirm the old rules still fail.** `test_legacy_normalizers_fail_the_canonical_contract` passes. If it fails, the golden identity was changed to match a legacy helper.
- **Proof.** Run `mkdir -p /tmp/wiki-pipeline-verify/evidence` and `set -o pipefail; python3 -m pytest tests/test_pipeline_contract.py -q --tb=short -k "canonical_url or wordpress or percent_decode or box_com or legacy_normalizers" | tee /tmp/wiki-pipeline-verify/evidence/canonical-url.txt; echo "exit:$?" | tee -a /tmp/wiki-pipeline-verify/evidence/canonical-url.txt`. The file contains the pytest summary and `exit:0`. `set -o pipefail` is required; without it `tee` hides a failing pytest.

## Gotchas

- `scripts/pipeline.py` `_normalize_url` still unquotes before `parse_qsl` and does not alias hosts. A green import of that function is not this feature.
- `"x.com" in host` is true for `box.com`. The contract compares the whole host.
- Stripping `s` on every host makes `test_wordpress_search_is_not_stripped` fail and matches the bug in 2734927.
- Decoding the raw URL and then calling `parse_qsl` turns `%2520` into a space. That is the double decode.
- Do not point this `-k` run at `tests/test_dedup.py` and report it as CanonicalUrl. That file covers the older helper.
