#!/bin/bash
set -euo pipefail

# Quartz Build Validation Script
# Usage: ./scripts/validate-build.sh [public_dir]
# Exit codes: 0 = pass, 1 = fail

PUBLIC_DIR="${1:-./public}"
ERRORS=0

echo "=== Quartz Build Validation ==="
echo "Public dir: $PUBLIC_DIR"
echo ""

# 1. Check index.html exists
if [[ ! -f "$PUBLIC_DIR/index.html" ]]; then
  echo "[FAIL] index.html missing"
  ERRORS=$((ERRORS + 1))
else
  echo "[PASS] index.html exists"
fi

# 2. Check 404.html exists
if [[ ! -f "$PUBLIC_DIR/404.html" ]]; then
  echo "[FAIL] 404.html missing"
  ERRORS=$((ERRORS + 1))
else
  echo "[PASS] 404.html exists"
fi

# 3. Check static assets
for asset in index.css postscript.js prescript.js; do
  if [[ ! -f "$PUBLIC_DIR/$asset" ]]; then
    echo "[FAIL] static asset missing: $asset"
    ERRORS=$((ERRORS + 1))
  else
    echo "[PASS] static asset exists: $asset"
  fi
done

# 4. Check all content pages have h1 (title)
# Find HTML files that are not index/404/tag/folder pages
CONTENT_PAGES=$(find "$PUBLIC_DIR" -name "*.html" -not -name "index.html" -not -name "404.html" | grep -E "(entities|concepts|atomic|moc|evergreen)/" || true)

H1_MISSING=0
for page in $CONTENT_PAGES; do
  if ! grep -q '<h1' "$page"; then
    echo "[WARN] No h1 in: ${page#$PUBLIC_DIR/}"
    H1_MISSING=$((H1_MISSING + 1))
  fi
done

if [[ $H1_MISSING -eq 0 ]]; then
  echo "[PASS] All content pages have h1"
else
  echo "[WARN] $H1_MISSING pages missing h1 (slug will be used as fallback)"
fi

# 5. Check property-meta on pages with frontmatter
# Sample: check if atomic notes have property-meta
ATOMIC_PAGES=$(find "$PUBLIC_DIR" -path "*/atomic/*.html" | head -5)
PROP_META_MISSING=0
for page in $ATOMIC_PAGES; do
  if ! grep -q 'class="property-meta"' "$page"; then
    PROP_META_MISSING=$((PROP_META_MISSING + 1))
  fi
done

if [[ $PROP_META_MISSING -eq 0 ]]; then
  echo "[PASS] Atomic notes have PropertyMeta"
else
  echo "[WARN] $PROP_META_MISSING sampled atomic pages missing PropertyMeta"
fi

# 6. Check for broken internal links (data-slug pointing to non-existent pages)
# Extract all internal links from HTML and verify target exists
if command -v python3 &>/dev/null; then
  BROKEN_LINKS=$(python3 -c "
import re, sys, os
from pathlib import Path

public_dir = Path('$PUBLIC_DIR')
broken = []

for html_file in public_dir.rglob('*.html'):
    content = html_file.read_text()
    # Find data-slug attributes
    slugs = re.findall(r'data-slug=\"([^\"]+)\"', content)
    for slug in slugs:
        target = public_dir / (slug + '.html')
        if not target.exists() and not (public_dir / slug / 'index.html').exists():
            broken.append(f'{html_file.relative_to(public_dir)} -> {slug}')

if broken:
    print('BROKEN:')
    for b in broken[:20]:
        print(f'  {b}')
    print(f'Total broken: {len(broken)}')
else:
    print('OK')
" 2>/dev/null || echo "python3 not available, skipping link check")

  if [[ "$BROKEN_LINKS" == OK ]]; then
    echo "[PASS] No broken internal links found"
  else
    echo "[WARN] Broken links detected:"
    echo "$BROKEN_LINKS"
  fi
else
  echo "[SKIP] python3 not available, skipping link check"
fi

echo ""
echo "=== Validation Complete ==="
echo "Errors: $ERRORS"

if [[ $ERRORS -gt 0 ]]; then
  echo "FAILED"
  exit 1
else
  echo "PASSED"
  exit 0
fi
