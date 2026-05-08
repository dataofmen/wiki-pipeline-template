---
name: note-pipeline
description: 6 Rs 노트 생성 파이프라인. clipping 원문을 받아 Karpathy LLM Wiki + Ars Contexta 방식으로 구조화된 지식 노트를 생성한다. "노트 만들어", "이거 정리해줘", "위키에 추가", "clipping 처리", "6 Rs 실행" 요청 시 사용. 단일 파일 처리와 배치 처리 모두 지원한다.
---

# Note Pipeline — 6 Rs 노트 생성 스킬

## 목적

원문 clipping을 받아 두 방법론을 통합한 고품질 지식 노트를 생성한다:
- **Karpathy**: 점진적 위키 누적, index.md + log.md 갱신, YAML 프론트매터
- **Ars Contexta**: 6 Rs 파이프라인, MOC 계층, 3공간 구조, 위키링크 네트워크

## 처리 흐름

```
clipping 파일 → R1 Reduce → R2 Reflect → R3 Reweave → R4 Record → R5 Verify
```

각 R 단계는 별도 프롬프트로 처리 (LLM 어텐션 분산 방지).

**노트 포맷 및 검증 체크리스트: `references/note-format.md` 를 반드시 읽고 따른다.**

## R1: Reduce — 통찰 추출 프롬프트

```
원문: {clipping 전체}

다음을 추출하라:
1. 핵심 주장 3~7개 (각 1~2문장)
2. 핵심 개념·용어 목록 (5~15개)
3. 배경 맥락 (2~3문장)
4. target: machine(구조적 팩트) | human(개인 인사이트) — 둘 다 가능
5. domain: ux | trading | pkm | general
6. 제안 제목 (한국어 또는 영어)
7. 태그 5~10개
```

## R2: Reflect — 연결 발견 프롬프트

```
R1 결과: {R1 output}
기존 노트 인덱스: {wiki/index.md + garden/index.md}

다음을 분석하라:
1. 연결 가능한 기존 노트 (최대 10개, 연결 이유 포함)
2. 신규 생성할 stub 노트 목록
3. 기존 노트와 모순 여부
```

## R3: Reweave — 기존 노트 갱신

R2에서 발견된 연결 노트들에:
- 역링크 추가: `- [[새노트]] — {연결 이유}`
- `updated` 날짜 갱신
- 새 정보로 보강할 섹션이 명확할 때만 보강

## R4: Record — 노트 작성

`references/note-format.md`의 해당 템플릿(wiki/ 또는 garden/)을 그대로 사용한다.

추가 원칙:
- 인용은 반드시 blockquote + 출처
- 위키링크는 실제 존재하는 파일만 (없으면 stub 생성)
- 한국어/영어 혼용 가능 (원문 언어 존중)

## R5: Verify — 검증

`references/note-format.md`의 "검증 체크리스트"를 순서대로 실행한다.

## log.md 기록

```markdown
## [{YYYY-MM-DD}] note | {제목}
- target: {machine | human}
- domain: {domain}
- 출처: {source_url}
- 연결: {N}개 | 새 노트: {N}개 | 기존 보강: {N}개
```

## 배치 처리

여러 clipping을 한 번에 처리할 때:
1. 각 clipping을 R1~R5 순서로 개별 처리
2. R2(Reflect)는 이미 생성된 새 노트도 인덱스에 포함하여 실행 (세션 내 누적)
3. R3(Reweave)는 세션 마지막에 일괄 실행 (중복 수정 방지)

## 참고

- 노트 포맷·검증 체크리스트: `references/note-format.md`