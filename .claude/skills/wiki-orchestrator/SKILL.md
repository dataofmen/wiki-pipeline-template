---
name: wiki-orchestrator
description: "{{SITE_NAME}} 정보처리 파이프라인 오케스트레이터. 텔레그램 입력 → 원문 저장 → 노트 생성 → Quartz 퍼블리시 전체 흐름을 조율한다. \"파이프라인 실행\", \"노트 생성\", \"퍼블리시\", \"위키 업데이트\", \"클리핑 처리\", \"정보 파이프라인\", \"하네스 실행\", \"inbox 처리\", \"재실행\", \"다시 퍼블리시\" 요청 시 반드시 이 스킬을 사용할 것. 신규 설정, 부분 재실행, 전체 파이프라인 실행 모두 지원한다."
---

# {{SITE_NAME}} Wiki 오케스트레이터

## 실행 모드: 하이브리드 파이프라인

- Phase 0~1: 단독 (컨텍스트 확인)
- Phase 2~5: 서브 에이전트 순차 파이프라인 (각 에이전트 결과가 다음 입력)
- Phase 6: 단독 (검증 & 보고)

---

## Phase 0: 컨텍스트 확인

실행 전 현재 상태를 파악한다.

```
상태 판별:
- inbox/에 미처리 파일 있음 → 파이프라인 실행
- _workspace/ 존재 + 사용자가 부분 재실행 요청 → 해당 단계만 실행
- 신규 설정 요청 → Phase 1 전체 실행
```

확인 항목:
1. `inbox/` 하위 미처리 파일 수 (`status: pending`)
2. `_workspace/` 하위 이전 실행 결과 존재 여부
3. Quartz 설치 여부 (`quartz/` 폴더 존재)
4. 텔레그램 봇 설정 존재 여부 (`scripts/.env` 파일)

---

## Phase 1: 초기 설정 (최초 1회)

Quartz 미설치 시 실행. 이후 건너뜀.

```bash
# Quartz 초기화
cd {{PROJECT_DIR}}
npx quartz@latest create
```

설정 완료 후 `quartz/quartz.config.ts` 수정:
- `references/quartz-config.md` 참조

텔레그램 봇 설정:
- `scripts/.env.example` → `scripts/.env` 복사 후 토큰 입력
- `.claude/skills/telegram-intake/SKILL.md` 참조

---

## Phase 2: Hermes — 수신 처리

**실행 모드:** 서브 에이전트

```
Agent(
  agents/hermes.md,
  prompt="inbox/ 미처리 파일 목록을 _workspace/hermes_new_files.jsonl에 기록하라.
         텔레그램 봇 데몬은 scripts/hermes_bot.py가 별도 실행 중임.
         이번 실행에서는 inbox/의 기존 pending 파일들을 처리하라.",
  model="opus"
)
```

완료 조건: `_workspace/hermes_new_files.jsonl` 생성

---

## Phase 3: Ingestor — 원문 추출

**실행 모드:** 서브 에이전트

```
Agent(
  agents/ingestor.md,
  prompt="_workspace/hermes_new_files.jsonl의 파일들을 읽어
         원문을 추출하고 clippings/에 저장하라.
         완료 목록을 _workspace/ingestor_done.jsonl에 기록하라.",
  model="opus"
)
```

완료 조건: `_workspace/ingestor_done.jsonl` 생성

---

## Phase 4: Note Maker — 노트 생성

**실행 모드:** 서브 에이전트

```
Agent(
  agents/note-maker.md,
  prompt="_workspace/ingestor_done.jsonl의 clipping들을 읽어
         6 Rs 파이프라인으로 노트를 생성하라.
         완료 목록을 _workspace/note_maker_done.jsonl에 기록하라.",
  model="opus"
)
```

완료 조건: `_workspace/note_maker_done.jsonl` 생성

---

## Phase 5: Publisher — Quartz 배포

**실행 모드:** 서브 에이전트

```
Agent(
  agents/publisher.md,
  prompt="_workspace/note_maker_done.jsonl의 새 노트들을 Quartz로 빌드·배포하라.
         결과를 _workspace/publisher_done.json에 기록하라.",
  model="opus"
)
```

완료 조건: `_workspace/publisher_done.json` 생성

---

## Phase 6: 보고 & 정리

실행 결과를 요약한다:

```
처리 결과 요약:
- 수신: {N}개 항목
- 클리핑: {N}개 완료 / {N}개 실패
- 노트 생성: {N}개 신규 / {N}개 기존 보강
- 퍼블리시: 성공 | 실패
- 사이트: {URL}
```

실패 항목이 있으면 `_workspace/errors.md`에 기록.
`_workspace/`를 `_workspace_prev/`로 이동 (감사 추적).

---

## 부분 재실행 가이드

특정 단계만 재실행 시:
- "클리핑만 다시" → Phase 3만 실행
- "노트만 다시" → Phase 4만 실행
- "퍼블리시만 다시" → Phase 5만 실행

---

## 에러 핸들링

| 실패 단계 | 처리 방법 |
|----------|----------|
| hermes | inbox 파일 직접 처리, 봇 재시작 안내 |
| ingestor | 실패 URL 목록 저장, 가용 clipping으로 진행 |
| note-maker | 실패 clipping 건너뜀, 성공 분만 배포 |
| publisher | 이전 빌드 유지, 에러 보고 |

---

## 테스트 시나리오

**정상 흐름:**
```
1. inbox/urls/에 테스트 URL 파일 생성
2. 오케스트레이터 실행
3. clippings/ 파일 생성 확인
4. wiki/ 또는 garden/ 노트 생성 확인
5. Quartz 빌드 성공 확인
```

**에러 흐름:**
```
1. 접근 불가 URL을 inbox/urls/에 저장
2. ingestor가 fetch_failed 상태로 저장 확인
3. 에러 건너뜀 후 나머지 파이프라인 정상 진행 확인
```