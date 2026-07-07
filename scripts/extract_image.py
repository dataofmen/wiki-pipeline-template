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

    gemini_bin = shutil.which("gemini") or "/opt/homebrew/bin/gemini"
    
    prompt = (
        f"Extract all the text and content from the image @{image_path.absolute()} accurately. "
        "Maintain the formatting, headings, bullet points, and any code blocks if present. "
        "If it is a diagram, describe its contents. "
        "Return ONLY the extracted markdown text without any conversational preamble."
    )

    try:
        process = subprocess.run(
            [gemini_bin, "-p", prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=300,  # gemini CLI can hang waiting for interactive input/auth
        )
        if process.returncode != 0:
            print(f"Error during Gemini CLI call: exit code {process.returncode}", file=sys.stderr)
            print(process.stderr, file=sys.stderr)
            sys.exit(1)
        
        # gemini CLI output sometimes includes ANSI or other things, but -p usually just outputs the text
        content = process.stdout.strip()
        if not content:
            print("Warning: Gemini CLI returned empty string.", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Error executing Gemini CLI: {e}", file=sys.stderr)
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

    frontmatter = f"""---
source_url: ""
source_type: image
clipped_at: "{datetime.now().isoformat()}"
title: "{title.replace('"', '')}"
fetch_method: gemini_cli
status: clipped
domain_hint: general
tags: []
created: {datetime.now().strftime('%Y-%m-%d')}
---
"""

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(frontmatter + content)

    print(f"Saved: {out_path.name}")

if __name__ == "__main__":
    main()
