#!/usr/bin/env python3
"""Atlas MOC Generator (Big Hubs + AI Synthesis).

This script identifies the top most connected notes (Big Hubs) across the vault,
uses an LLM to synthesize a narrative describing the current landscape,
and injects the result into the index.md homepage.
"""

from __future__ import annotations

import re
import os
import sys
import json
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
INDEX_MD = PROJECT_DIR / "index.md"


def _resolve_codex_bin() -> str:
    """Resolve the `codex` CLI binary, augmenting PATH for launchd daemons."""
    search_path = ":".join([
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/Applications/cmux.app/Contents/Resources/bin",
        os.environ.get("PATH", "/usr/bin:/bin"),
    ])
    return shutil.which("codex", path=search_path) or "codex"


CODEX_BIN = _resolve_codex_bin()

MAX_HUBS = 30

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


@dataclass
class Note:
    cognitive_type: str
    title: str
    summary: str
    slug: str
    rel: str
    target: str
    type: str
    created: str
    updated: str
    tags: list[str]
    outlinks: set[str] = field(default_factory=set)
    inlinks: set[str] = field(default_factory=set)
    relations: list[dict] = field(default_factory=list)

    @property
    def date(self) -> str:
        return self.updated or self.created or ""

    @property
    def link_count(self) -> int:
        return len(self.inlinks) + len(self.outlinks)


import yaml

def _parse_scalar(value: str) -> str:
    return value.strip().strip('"\'')


def _parse_list(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
        return [_parse_scalar(part) for part in value.split(",") if part.strip()]
    return [_parse_scalar(value)]


def _parse_frontmatter(text: str) -> dict:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}

    try:
        # UX Evidence Graph에서 추가된 pending_relations와 같은 중첩 YAML 구조를 파싱하기 위해 pyyaml 사용
        fm = yaml.safe_load(match.group(1))
        return fm if isinstance(fm, dict) else {}
    except yaml.YAMLError:
        # Fallback to simple parser if pyyaml fails
        fm = {}
        for line in match.group(1).splitlines():
            if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "tags":
                fm[key] = _parse_list(value)
            else:
                fm[key] = _parse_scalar(value)
        return fm


def _body_without_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1).strip()


def _clean_inline_markdown(line: str) -> str:
    line = re.sub(r"\*+([^*]+)\*+", r"\1", line)
    line = re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), line)
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def _one_line_summary(text: str) -> str:
    body = _body_without_frontmatter(text)
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(">") or line.startswith("[["):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        clean = _clean_inline_markdown(line)
        if len(clean) > 20:
            # 첫 줄(단락) 전체를 요약으로 추출하여 온전한 내용을 전달
            if clean and clean[-1] in "다음함기" and not clean.endswith("."):
                clean += "."
                
            return clean
    return ""


def _normalize_link_target(target: str) -> str:
    target = target.strip().replace("\\", "/")
    if target.endswith(".md"):
        target = target[:-3]
    return Path(target).stem


def _extract_outlinks(text: str) -> set[str]:
    return {_normalize_link_target(m.group(1)) for m in WIKILINK_RE.finditer(text)}


def _note_link(note: Note) -> str:
    return f"[[{note.rel}|{note.title}]]"


def _collect_notes(project_dir: Path) -> list[Note]:
    roots = [project_dir / "wiki", project_dir / "garden"]
    notes: list[Note] = []

    for root in roots:
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.md"), reverse=True):
            if f.name == "index.md" or f.name.startswith("moc-"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            fm = _parse_frontmatter(text)
            status = str(fm.get("status", "publish"))
            if status and status != "publish":
                continue

            rel = str(f.relative_to(project_dir).with_suffix(""))
            
            fm_relations = fm.get("relations", [])
            if not isinstance(fm_relations, list): fm_relations = []
            fm_pending = fm.get("pending_relations", [])
            if not isinstance(fm_pending, list): fm_pending = []
            all_relations = fm_relations + fm_pending
            
            note = Note(
                cognitive_type=str(fm.get("cognitive_type", "observations") or "observations"),
                title=str(fm.get("title", f.stem) or f.stem),
                summary=_one_line_summary(text),
                slug=f.stem,
                rel=rel,
                target=str(fm.get("target", "")),
                type=str(fm.get("type", "")),
                created=str(fm.get("created", "")),
                updated=str(fm.get("updated", "")),
                tags=[t for t in fm.get("tags", []) if isinstance(t, str)] if isinstance(fm.get("tags"), list) else [],
                outlinks=_extract_outlinks(text),
                relations=all_relations,
            )
            notes.append(note)

    by_slug: dict[str, list[Note]] = defaultdict(list)
    for note in notes:
        by_slug[note.slug].append(note)

    for note in notes:
        for target in note.outlinks:
            for linked in by_slug.get(target, []):
                if linked.slug != note.slug or linked.rel != note.rel:
                    linked.inlinks.add(note.slug)

    return notes


def _note_sort_key(note: Note) -> tuple[int, str, str]:
    return (note.link_count, note.date, note.title)


def _generate_ai_synthesis(hubs: list[Note]) -> str:
    if not hubs:
        return "데이터가 부족하여 요약을 생성할 수 없습니다."
        
    hub_info = []
    for i, n in enumerate(hubs):
        info = f"{i+1}. 제목: {n.title}\n   태그: {', '.join(n.tags)}\n   유형: {n.cognitive_type}\n   요약: {n.summary}"
        if n.relations:
            rel_strs = [f"[{r.get('type')}] -> {r.get('target')}" for r in n.relations if isinstance(r, dict)]
            if rel_strs:
                info += f"\n   명시적 관계: {'; '.join(rel_strs)}"
        hub_info.append(info)
        
    hub_text = "\n\n".join(hub_info)
    
    prompt = f"""당신은 이 지식 위키(Digital Garden)의 편집장입니다.
아래는 현재 위키 전체에서 가장 다른 노트들과 많이 연결된(인용된) "상위 {len(hubs)}개의 빅 허브" 노트 목록입니다.

[빅 허브 목록]
{hub_text}

[요청 사항]
이 핵심 노트들을 분석하여, "현재 이 위키의 지식 생태계가 어떤 방향으로 흘러가고 있는지, 어떤 핵심 주제들이 서로 교차하고 있는지"를 파악하는 지형도 요약본(Synthesis)을 작성하세요.

[출력 규칙]
1. 분량: 2~3문단 (너무 길지 않게 핵심만 서술)
2. 어조: 전문가적이고 분석적인 평서문 (예: "~하는 흐름이 형성되고 있다", "~가 중심을 이룬다")
3. 강조: 중요한 키워드는 반드시 HTML `<strong>` 태그를 사용해 강조할 것 (예: <strong>키워드</strong>와 같이 조사와 붙여 쓰기). 일반 마크다운 별표(**)는 파싱 오류가 생기므로 절대 사용 금지.
4. 인사말이나 불필요한 서론/결론("분석 결과는 다음과 같습니다" 등) 없이 바로 본론부터 시작할 것.
"""
    # codex exec prints a session log to stdout; -o writes just the final
    # message to a file, so read that instead of parsing stdout.
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(mode="r", suffix=".md", delete=False) as tf:
            out_path = tf.name
        cmd = [CODEX_BIN, "exec", "--skip-git-repo-check", "-o", out_path, prompt]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            synthesis = Path(out_path).read_text(encoding="utf-8").strip()
            Path(out_path).unlink(missing_ok=True)
            if synthesis:
                return synthesis
        else:
            print(f"Codex synthesis exited {result.returncode}: {result.stderr[:300]}")
        Path(out_path).unlink(missing_ok=True)
    except Exception as e:
        print(f"Codex AI synthesis failed: {e}")

    return "AI 지형도 요약을 생성하지 못했습니다. (연결 오류)"


def generate_moc(project_dir: Path = PROJECT_DIR) -> None:
    print("Collecting notes and calculating centrality...")
    notes = _collect_notes(project_dir)
    
    # 1. Sort by link_count and get top hubs
    hubs = sorted(notes, key=_note_sort_key, reverse=True)[:MAX_HUBS]
    
    # 2. Extract old synthesis if it exists (to prevent overwriting with error on quota failure)
    old_synthesis = ""
    if INDEX_MD.exists():
        content = INDEX_MD.read_text(encoding="utf-8")
        if "<!-- ATLAS_START -->" in content and "### 🔗 현재 생태계의 중심" in content:
            start_idx = content.find("<!-- ATLAS_START -->")
            end_idx = content.find("### 🔗 현재 생태계의 중심")
            old_chunk = content[start_idx:end_idx]
            # Try to extract just the text after the date
            match = re.search(r"> \*\*오늘의 지식 지형도 요약\*\*[^\n]*\n(.*)", old_chunk, re.DOTALL)
            if match:
                old_synthesis = match.group(1).strip()
    
    # 3. Generate AI Synthesis
    print("Synthesizing AI narrative...")
    synthesis = _generate_ai_synthesis(hubs)
    
    if "오류" in synthesis and old_synthesis:
        synthesis = old_synthesis
    
    # 4. Format the MOC injection text
    now = datetime.now()
    lines = [
        "<!-- ATLAS_START -->",
        f"> **오늘의 지식 지형도 요약** (자동 생성됨 · {now.strftime('%Y-%m-%d %H:%M')})",
        "",
        synthesis,
        "",
        "### 🔗 현재 생태계의 중심 (Top 30 Hubs)",
        ""
    ]
    
    for n in hubs:
        lines.append(f"- [[{n.rel}|{n.title}]] — (연결: {n.link_count}) {n.summary}")
        
    lines.append("<!-- ATLAS_END -->")
    injection_text = "\n".join(lines)
    
    # 5. Inject into index.md
    if INDEX_MD.exists():
        content = INDEX_MD.read_text(encoding="utf-8")
        if "<!-- ATLAS_START -->" in content and "<!-- ATLAS_END -->" in content:
            start_idx = content.find("<!-- ATLAS_START -->")
            end_idx = content.find("<!-- ATLAS_END -->") + len("<!-- ATLAS_END -->")
            
            new_content = content[:start_idx] + injection_text + content[end_idx:]
            INDEX_MD.write_text(new_content, encoding="utf-8")
            print("Successfully injected Atlas MOC into index.md")
        else:
            print("Error: ATLAS markers not found in index.md")

    # 5. Clear all obsolete MOC files in garden
    for old_moc in (project_dir / "garden").glob("moc-*.md"):
        old_moc.unlink()
        print(f"Deleted old MOC: {old_moc.name}")

if __name__ == "__main__":
    generate_moc()
    print("MOC generation complete.")
