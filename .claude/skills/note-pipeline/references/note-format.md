# Note Format — 단일 진실 원천

노트 포맷 변경 시 이 파일만 수정한다. note-maker 에이전트와 note-pipeline 스킬 모두 이 파일을 참조한다.

---

## 공통 프론트매터 필드

모든 노트(wiki/ + garden/ 공통)에 반드시 포함:

```yaml
title: {노트 제목}
target: machine | human          # wiki/ = machine, garden/ = human
domain: ux | trading | pkm | general
type: concept | entity | analysis | insight | procedure
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags: [태그1, 태그2]
source_clipping: "[[clippings/{파일명없이-확장자포함}]]"
source_url: https://...
status: draft | active | needs-review
```

**필수 규칙:**
- `source_clipping`은 반드시 `[[clippings/...]]` 위키링크 형식. 이 링크가 Quartz 그래프 엣지를 만든다.
- `status: draft`로 시작, 검토 후 `active`로 변경.
- `updated`는 노트 수정 시마다 갱신.

---

## wiki/ 노트 — 구조 중심 (target: machine)

AI 에이전트가 읽는다. 팩트·정의·속성·관계를 밀도 있게 담는다. 서사 최소화.

### wiki/ 서브디렉토리 배치 규칙

`type` 값에 따라 저장 경로가 달라진다:

| type | 저장 경로 | 설명 |
|------|----------|------|
| `concept` | `wiki/concepts/` | 개념·이론·패턴 정의 |
| `entity` | `wiki/entities/` | 인물·도구·프레임워크·조직 |
| `procedure` | `wiki/` 루트 | 절차·방법론 (서브디렉토리 없음) |

**index.md 갱신도 경로에 맞게:** `wiki/concepts/20260508-xxx.md`를 생성했으면 `wiki/index.md`의 링크도 `[[concepts/20260508-xxx]]`로 작성한다.

```markdown
---
title: {제목}
target: machine
domain: {ux | trading | pkm | general}
type: {concept | entity | procedure}
created: {YYYY-MM-DD}
updated: {YYYY-MM-DD}
tags: [{태그1}, {태그2}]
source_clipping: "[[clippings/{파일명}]]"
source_url: {URL}
status: draft
---

## 정의

{1~2문장. 명확하고 간결하게.}

## 핵심 속성

- **{속성명}**: {값}
- **{속성명}**: {값}

## 관계

- [[{연관 노트}]] — {관계 유형: 상위개념 | 하위개념 | 대조 | 연장}
- [[{연관 노트}]] — {관계 유형}

## 인용

> {원문의 핵심 문장을 그대로 인용}

## 출처

[[clippings/{파일명}]] · [{source_url}]({source_url})
```

**wiki/ 작성 원칙:**
- **`# 제목` 헤딩 금지.** Quartz가 frontmatter `title:`로 자동 렌더링하므로 본문에 넣으면 두 번 출력된다. 본문은 `## 정의`로 시작한다.
- 헤더 구조 고정 (정의 → 핵심 속성 → 관계 → 인용 → 출처)
- 의견·감상 금지. 팩트만.
- 관계 섹션: 최소 1개 링크 (고아 노트 금지)
- 길이: 200~500단어

---

## garden/ 노트 — 아토믹 노트 (target: human)

내가 읽고 탐색한다. **하나의 노트 = 하나의 아이디어.** 개인 관점·발견·연결을 담는다.

### 아토믹 노트 원칙

- **하나의 클리핑 → 여러 개의 garden 노트.** 핵심 인사이트가 3개면 노트도 3개 생성한다.
- **제목은 완전한 문장(주장/명제)** 으로 작성한다. 주제명이 아니라 그 자체로 의미가 통하는 주장이어야 한다.
  - ❌ 나쁜 예: `"LLM 패턴을 읽고"`, `"Eugene Yan의 7패턴"`
  - ✅ 좋은 예: `"데모는 쉽지만 제품은 어렵다 — Evals 없이는 개선 방향을 알 수 없다"`
  - ✅ 좋은 예: `"캐싱은 비용 절감이 아니라 반응 속도의 문제다"`
  - ✅ 좋은 예: `"LLM 위키는 지식이 누적될수록 더 정확해진다"`
- **길이: 150~400단어.** 길어지면 쪼개는 신호다.
- 노트가 자립해야 한다 — 소스 없이 읽어도 의미가 통해야 한다.

```markdown
---
title: {완전한 문장으로 된 주장/명제}
target: human
domain: {ux | trading | pkm | general}
type: {insight | analysis}
created: {YYYY-MM-DD}
updated: {YYYY-MM-DD}
tags: [{태그1}, {태그2}]
source_clipping: "[[clippings/{파일명}]]"
source_url: {URL}
status: draft
---

{2~4문장. 이 주장이 왜 중요한지, 어떤 맥락에서 나왔는지.}

## 근거

{원문의 핵심 구절 인용 또는 논리적 근거.}

> {원문 인용 (있을 경우)}

## 연결된 생각

- [[{연관 노트}]] — {왜 연결되는지}

## 출처

[[clippings/{파일명}]] · [{source_url}]({source_url})
```

**garden/ 작성 원칙:**
- **`# 제목` 헤딩 금지.** Quartz가 frontmatter `title:`로 자동 렌더링. 본문은 바로 서사 텍스트로 시작한다.
- 1인칭 허용. 개인 관점 환영.
- "근거" 섹션이 핵심 — 주장을 지지하는 증거를 담는다.
- "연결된 생각"은 최소 1개. 고아 노트 금지.
- 길이: 150~400단어 (이 이상이면 두 개로 쪼개라)

---

## 하나의 clipping → 두 노트 생성 가능

동일 clipping에서 wiki/ 노트와 garden/ 노트를 모두 생성할 수 있다.

예: Karpathy LLM Wiki 글을 받으면
- `wiki/llm-wiki-pattern.md` — 방법론 정의·속성 (AI 컨텍스트용)
- `garden/karpathy-wiki-읽고.md` — 개인 인사이트 (탐색용)

두 노트 모두 `[[clippings/20260508-karpathy-llm-wiki]]`를 포함 → 그래프에서 clipping이 허브 역할.

---

## index.md 갱신 규칙

**index.md에는 수동 노트 목록 테이블을 작성하지 않는다.** Quartz가 폴더 내 파일 목록을 자동 생성한다. 수동 테이블을 추가하면 동일 내용이 두 번 표시된다.

index.md는 **frontmatter + 폴더 설명 텍스트만** 포함한다:

```markdown
---
title: {폴더 제목}
---

{폴더의 역할을 1~2문장으로 설명}
```

`log.md`에는 처리 이력을 계속 append한다 (Quartz가 자동 생성하지 않는 유일한 수동 파일).

---

## 슬러그 규칙

`{YYYYMMDD}-{영문-핵심단어-3~5개-kebab-case}.md`

**영문 슬러그 필수.** 한글 파일명은 GitHub Pages URL이 `%EB%8D%...` 형태로 인코딩되어 공유 불가.

예:
- `20260508-ema-crossover-strategy.md`
- `20260508-llm-wiki-compounds-with-accumulation.md`
- `20260508-atomic-notes-enable-context-injection.md`

한글 제목은 frontmatter `title:` 필드에 그대로 유지한다.

---

## 검증 체크리스트 (R5 Verify 단계)

생성된 노트마다 확인:

- [ ] 프론트매터 필수 필드 모두 존재 (`title`, `target`, `domain`, `type`, `created`, `source_clipping`, `source_url`, `status`)
- [ ] `source_clipping`이 `[[clippings/...]]` 형식
- [ ] 해당 clipping 파일 실제 존재
- [ ] `관계` 또는 `연결된 생각` 섹션에 위키링크 최소 1개
- [ ] 위키링크 대상 파일 존재 (없으면 stub 생성)
- [ ] 출처 섹션 포함
- [ ] index.md에 항목 추가됨
- [ ] log.md에 기록됨