"""
PDF 원문 추출 스크립트
사용법: python3 extract_pdf.py "{PDF경로}" [--output clippings/]
"""
import sys
import re
import argparse
from pathlib import Path
from datetime import datetime


def slug_from_title(title: str) -> str:
    title = re.sub(r"[^\w\s-]", "", title.lower())
    words = title.split()[:5]
    return "-".join(words)


def _smart_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        # Ignore comments, markdown images, and very short lines
        if line and not line.startswith("<!--") and not line.startswith("![") and len(line) > 5:
            # If it looks like a reasonable title (not a url, not too long)
            if len(line) < 100 and "http" not in line:
                return line
    return fallback


def extract_with_pdfplumber(pdf_path: Path) :
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            title = pdf.metadata.get("Title", "") or pdf_path.stem
            pages = []
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                pages.append(f"<!-- page {i+1} -->\n{text}")
            content = "\n\n".join(pages)
            if not title or title.lower().startswith("microsoft") or title.lower().startswith("untitled"):
                title = _smart_title(content, title)
            return title, content
    except Exception as e:
        print(f"pdfplumber 실패: {e}", file=sys.stderr)
    return None


def extract_with_pymupdf(pdf_path: Path) :
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(pdf_path))
        title = doc.metadata.get("title", "") or pdf_path.stem
        pages = []
        for i, page in enumerate(doc):
            pages.append(f"<!-- page {i+1} -->\n{page.get_text()}")
        doc.close()
        content = "\n\n".join(pages)
        if not title or title.lower().startswith("microsoft") or title.lower().startswith("untitled"):
            title = _smart_title(content, title)
        return title, content
    except Exception as e:
        print(f"pymupdf 실패: {e}", file=sys.stderr)
    return None


def detect_domain_hint(title: str, content: str) -> str:
    text = f"{title} {content[:500]}".lower()
    if any(k in text for k in ["trading", "invest", "stock", "crypto", "quant", "backtest", "sharpe"]):
        return "trading"
    if any(k in text for k in ["ux", "hci", "usability", "user research", "product", "design"]):
        return "ux"
    if any(k in text for k in ["note", "knowledge", "pkm", "zettelkasten", "philosophy", "essay"]):
        return "pkm"
    return "general"


def save_clipping(pdf_path: Path, title: str, content: str, output_dir: Path) -> Path:
    date = datetime.now().strftime("%Y%m%d")
    slug = slug_from_title(title) or pdf_path.stem[:30]
    filename = f"{date}-{slug}.md"
    domain_hint = detect_domain_hint(title, content)
    word_count = len(content.split())

    front_matter = f"""---
source_url: file://{pdf_path.resolve()}
source_type: pdf
clipped_at: {datetime.now().isoformat()}
title: {title}
author: ""
word_count: {word_count}
fetch_method: pdf
status: clipped
domain_hint: {domain_hint}
tags: []
---

{content}

---
*원문 PDF: {pdf_path.name}*
"""
    path = output_dir / filename
    path.write_text(front_matter)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    output_dir = Path(args.output) if args.output else Path(__file__).parent.parent / "clippings"
    output_dir.mkdir(exist_ok=True)

    if not pdf_path.exists():
        print(f"파일 없음: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    result = extract_with_pdfplumber(pdf_path) or extract_with_pymupdf(pdf_path)

    if result:
        title, content = result
    else:
        title = pdf_path.stem
        content = f"[PDF 추출 실패 — 수동 처리 필요]\n파일: {pdf_path}"

    path = save_clipping(pdf_path, title, content, output_dir)
    print(f"저장: {path}")


if __name__ == "__main__":
    main()
