#!/bin/bash
# Wiki Pipeline venv setup
# Usage: bash scripts/setup_venv.sh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
ULTIMATE_FETCHER_DIR="${ULTIMATE_FETCHER_DIR:-$HOME/Projects/ultimate-fetcher}"

echo "=== Wiki Pipeline venv setup ==="
echo "Project: $PROJECT_DIR"
echo "Venv:    $VENV_DIR"

# Create venv
if [ -d "$VENV_DIR" ]; then
    echo "Venv already exists at $VENV_DIR, upgrading pip..."
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
else
    echo "Creating venv..."
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
fi

# Install ultimate-fetcher
if [ -d "$ULTIMATE_FETCHER_DIR" ]; then
    echo "Installing ultimate-fetcher from local path: $ULTIMATE_FETCHER_DIR"
    pip install -e "$ULTIMATE_FETCHER_DIR"
else
    echo "ultimate-fetcher not found at $ULTIMATE_FETCHER_DIR"
    echo "Set ULTIMATE_FETCHER_DIR env var or install manually:"
    echo "  pip install git+https://github.com/dataofmen/ultimate-fetcher.git"
fi

# Install pipeline dependencies
echo "Installing pipeline dependencies..."
# requests: Telegram notifications. openai: legacy (note generation now uses
# Codex CLI — kept only for backward compatibility).
pip install pdfplumber pymupdf python-dotenv watchdog pyyaml requests openai

# Install Playwright browsers (needed by ultimate-fetcher fallback)
echo "Installing Playwright chromium..."
playwright install chromium 2>/dev/null || echo "Playwright install skipped (install manually if needed)"

echo ""
echo "=== Setup complete ==="
echo "Activate venv: source $VENV_DIR/bin/activate"
echo "Run pipeline:  python3 scripts/pipeline.py --phase 3-6"

if ! command -v codex >/dev/null 2>&1; then
    echo "Warning: Codex CLI is required for Phase 4. Install it, then run: codex login"
fi
