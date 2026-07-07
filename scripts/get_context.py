"""
AI 에이전트 컨텍스트 추출 스크립트
사용법:
  python3 get_context.py --domain trading          # domain 태그 필터
  python3 get_context.py --target machine          # wiki/ 전체
  python3 get_context.py --domain trading --format summary  # 요약만
"""
import sys
import yaml
import argparse
from pathlib import Path

WIKI_ROOT = Path(__file__).parent.parent


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}


def get_note_body(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].strip() if len(parts) >= 3 else text


def collect_notes(domain = None, target = None) -> list[dict]:
    search_dirs = []
    if target == "machine" or target is None:
        search_dirs.append(WIKI_ROOT / "wiki")
    if target == "human" or target is None:
        search_dirs.append(WIKI_ROOT / "garden")

    results = []
    for d in search_dirs:
        if not d.exists():
            continue
        for md in sorted(d.rglob("*.md")):
            if md.name == "index.md":
                continue
            text = md.read_text(encoding="utf-8")
            front = parse_frontmatter(text)
            if domain and front.get("domain") != domain:
                continue
            results.append({
                "path": str(md.relative_to(WIKI_ROOT)),
                "title": front.get("title", md.stem),
                "domain": front.get("domain", ""),
                "target": front.get("target", ""),
                "tags": front.get("tags", []),
                "relations": front.get("relations", []) + front.get("pending_relations", []),
                "body": get_note_body(text),
                "source_url": front.get("source_url", ""),
            })
    return results


def format_context(notes: list[dict], fmt: str) -> str:
    if fmt == "summary":
        lines = []
        for n in notes:
            lines.append(f"## {n['title']} [{n['domain']}]")
            # 첫 3줄만
            body_lines = [l for l in n["body"].splitlines() if l.strip()][:3]
            lines.extend(body_lines)
            lines.append("")
        return "\n".join(lines)

    elif fmt == "titles":
        return "\n".join(f"- {n['title']} ({n['path']})" for n in notes)

    elif fmt == "graph":
        lines = ["# Knowledge Graph Context"]
        for n in notes:
            rels = n.get("relations", [])
            if rels:
                lines.append(f"## {n['title']} (Domain: {n['domain']})")
                for r in rels:
                    if isinstance(r, dict):
                        lines.append(f"- [{r.get('type')}] -> {r.get('target')} (Evidence: {r.get('evidence')})")
        return "\n".join(lines)

    else:  # full
        parts = []
        for n in notes:
            rel_str = ""
            if n.get("relations"):
                rel_str = "\n### Relations\n" + "\n".join(f"- [{r.get('type')}] -> {r.get('target')} : {r.get('evidence')}" for r in n["relations"] if isinstance(r, dict)) + "\n"
            parts.append(f"# {n['title']}{rel_str}\n\n{n['body']}")
        return "\n\n---\n\n".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default=None, help="domain 태그 필터 (예: trading, ux)")
    parser.add_argument("--target", default=None, choices=["machine", "human"], help="wiki/ 또는 garden/ 필터")
    parser.add_argument("--format", default="full", choices=["full", "summary", "titles", "graph"])
    parser.add_argument("--max-tokens", type=int, default=None, help="대략적 토큰 한계 (단어 수 기준)")
    args = parser.parse_args()

    notes = collect_notes(domain=args.domain, target=args.target)

    if not notes:
        print(f"[get_context] 해당 조건의 노트 없음: domain={args.domain}, target={args.target}", file=sys.stderr)
        sys.exit(0)

    output = format_context(notes, args.format)

    if args.max_tokens:
        words = output.split()
        if len(words) > args.max_tokens:
            output = " ".join(words[:args.max_tokens]) + "\n\n[...토큰 한계로 잘림]"

    print(output)
    print(f"\n[get_context] {len(notes)}개 노트 반환", file=sys.stderr)


if __name__ == "__main__":
    main()
