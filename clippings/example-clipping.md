---
title: "How to Build a Knowledge Graph with Markdown"
source: "https://example.com/knowledge-graph-markdown"
type: article
domain: pkm
created: 2025-01-01
tags:
  - pkm
  - graph
  - markdown
---

# How to Build a Knowledge Graph with Markdown

> 이것은 예시 클리핑입니다. 원문 추출 파이프라인(Jina Reader, readability 등)이 URL에서 자동 생성하는 형식을 보여줍니다.

## 원문 발췌

지식 그래프를 마크다운으로 구축하는 방법은 간단하다. 각 노트를 하나의 파일로 만들고, `[[위키링크]]`로 연결한다. 정적 사이트 생성기(Quartz, Obsidian Publish 등)가 이 연결망을 시각화한다.

핵심은 세 가지다:

1. **원자 노트**: 하나의 아이디어, 하나의 파일
2. **위키링크**: `[[다른 노트]]`로 연결
3. **프론트매터**: 태그, 도메인, 날짜로 메타데이터 부여

이 구조에서 노트는 디렉토리가 아니라 연결에 의해 조직된다.

---

> `clippings/` 폴더는 원문을 그대로 저장하는 공간입니다. 위키링크로 `wiki/`와 `garden/` 노트에서 참조됩니다.