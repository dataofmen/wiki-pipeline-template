---
name: quartz-publish
description: Quartz 퍼블리시 스킬. 마크다운 노트를 Quartz 정적 사이트로 빌드하고 GitHub Pages에 배포한다. "퍼블리시", "사이트 배포", "Quartz 빌드", "위키 게시", "홈페이지 업데이트" 요청 시 사용.
---

# Quartz Publish — 퍼블리시 스킬

## Quartz 초기 설치 (최초 1회)

```bash
cd {{PROJECT_DIR}}
git clone https://github.com/jackyzha0/quartz.git quartz
cd quartz
npm install
```

`references/quartz-config.md`의 설정을 `quartz/quartz.config.ts`에 적용.

## 콘텐츠 동기화

wiki 폴더 → Quartz content 폴더로 복사:

```bash
# scripts/sync_content.sh (직접 실행 가능)
bash scripts/sync_content.sh
```

sync_content.sh 내용 (참고용):
```bash
for folder in wiki garden clippings; do
  rsync -av --delete "$WIKI_ROOT/$folder/" "$QUARTZ_CONTENT/$folder/"
done
cp "$WIKI_ROOT/index.md" "$QUARTZ_CONTENT/index.md"
```

## 빌드

```bash
cd quartz
npx quartz build
```

빌드 결과: `quartz/public/`

## 배포

### GitHub Pages (권장)
```bash
cd quartz
npx quartz sync
```

Quartz의 자동 GitHub Pages 배포 (`gh-pages` 브랜치).

### 로컬 미리보기
```bash
cd quartz
npx quartz build --serve
```

브라우저: `http://localhost:8080`

## 홈페이지 index.md 자동 생성

퍼블리시 전 `index.md`를 최신 노트 수로 갱신:

```markdown
---
title: {{SITE_NAME}} wiki
---

# {{SITE_NAME}} wiki

## 영역

- [[wiki/index|wiki]] — 구조화 지식 베이스 ({N}개 노트)
- [[garden/index|garden]] — 생각 정원 ({N}개 노트)
- [[clippings/index|clippings]] — 원문 클리핑 ({N}개)

*마지막 업데이트: {YYYY-MM-DD HH:MM}*
```

## Broken Link 검사

빌드 전 실행:
```bash
python3 scripts/check_links.py
```

깨진 링크 발견 시: 경고 출력 후 빌드 계속 (빌드 중단 금지).

## 배포 후 검증

```bash
# 사이트 응답 확인
curl -sI "https://{{GITHUB_USERNAME}}.github.io/{{REPO_NAME}}/" | head -5
```

## 참고

- Quartz 설정 상세: `references/quartz-config.md`
- GitHub Pages 배포: `npx quartz sync` 실행 (Quartz 내장 기능, 별도 설정 파일 불필요)