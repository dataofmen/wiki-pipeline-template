"""
Image OCR extraction script
Usage: python3 extract_image.py "{IMAGE_PATH}" [--output clippings/]
"""
import sys
import os
import re
import json
import base64
import argparse
from pathlib import Path

from content_validation import validate_content
from datetime import datetime

from openai import OpenAI

def slug_from_title(title: str) -> str:
    title = re.sub(r"[^\w\s-]", "", title.lower())
    words = title.split()[:5]
    return "-".join(words)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image_path", help="Path to the image file")
    parser.add_argument("--output", default="clippings", help="Output directory")
    args = parser.parse_args()

    image_path = Path(args.image_path)
    output_dir = Path(args.output)
    
    if not image_path.exists():
        print(f"Error: file not found {image_path}", file=sys.stderr)
        sys.exit(1)

    import subprocess
    import shutil
    import tempfile

    codex_bin = shutil.which("codex", path=":".join([
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/Applications/cmux.app/Contents/Resources/bin",
        os.environ.get("PATH", "/usr/bin:/bin"),
    ])) or "codex"

    prompt = (
        "Extract all the text and content from the attached image accurately. "
        "Maintain the formatting, headings, bullet points, and any code blocks if present. "
        "If it is a diagram, describe its contents. "
        "Return ONLY the extracted markdown text without any conversational preamble."
    )

    try:
        # codex exec prints a session log to stdout; -o writes just the final
        # message to a file, so read that instead of parsing stdout.
        with tempfile.NamedTemporaryFile(mode="r", suffix=".md", delete=False) as tf:
            out_path = tf.name
        process = subprocess.run(
            [codex_bin, "exec", "--skip-git-repo-check",
             "-i", str(image_path.absolute()), "-o", out_path, prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=300,  # never let a hung CLI stall the pipeline
        )
        if process.returncode != 0:
            print(f"Error during Codex CLI call: exit code {process.returncode}", file=sys.stderr)
            print(process.stderr, file=sys.stderr)
            Path(out_path).unlink(missing_ok=True)
            sys.exit(1)

        content = Path(out_path).read_text(encoding="utf-8", errors="ignore").strip()
        Path(out_path).unlink(missing_ok=True)
        if not content:
            print("Warning: Codex CLI returned empty string.", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Error executing Codex CLI: {e}", file=sys.stderr)
        sys.exit(1)

    lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("![")]
    title = lines[0] if lines else image_path.stem
    title = title.replace("#", "").strip()

    # INLINE VALIDATION
    if len(content.strip()) < 20:
        print(f"Error: Extracted content is too short ({len(content.strip())} chars). Rejecting extraction.", file=sys.stderr)
        sys.exit(1)
        
    if not title or title == "0" or title == "(0)":
        print(f"Error: Extracted title is invalid ('{title}'). Rejecting extraction.", file=sys.stderr)
        sys.exit(1)

    slug = slug_from_title(title)
    if not slug:
        slug = f"image-{datetime.now().strftime('%H%M%S')}"

    date_str = datetime.now().strftime("%Y%m%d")
    out_filename = f"{date_str}-{slug}.md"
    out_path = output_dir / out_filename

    # VERIFY 게이트 — OCR/비전 추출이 빈 결과를 남기면 clipped 로 두지 않는다.
    validation = validate_content(content, source_type="image", title=title)
    warning_lines = ""
    if not validation.ok:
        warning_lines = (f'content_warning: {validation.code}\n'
                         f'content_warning_reason: "{validation.reason}"\n')
    status = "fetch_failed" if validation.failed else "clipped"

    frontmatter = f"""---
source_url: ""
source_type: image
clipped_at: "{datetime.now().isoformat()}"
title: "{title.replace('"', '')}"
fetch_method: codex_cli
word_count: {validation.word_count}
status: {status}
domain_hint: general
tags: []
created: {datetime.now().strftime('%Y-%m-%d')}
{warning_lines}---
"""

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frontmatter + content)

    print(f"Saved: {out_path.name}")

if __name__ == "__main__":
    main()
