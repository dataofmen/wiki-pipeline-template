---
name: content-extraction
description: 원문 추출 스킬. URL 웹페이지, PDF 파일, X.com/Twitter 스레드에서 원문 텍스트를 추출하여 clippings/ 폴더에 저장한다. "URL 저장", "페이지 클리핑", "PDF 처리", "트위터 스레드 저장", "원문 추출" 요청 시 반드시 사용. Jina Reader, readability, yt-dlp, Playwright 방법을 순서대로 시도한다.
---

# Content Extraction — 원문 추출 스킬

## 추출 방법론 우선순위

### URL 처리

1. **Jina Reader** (1차 시도, 최우선)
   ```bash
   curl "https://r.jina.ai/{URL}" -H "Accept: text/markdown"
   ```

2. **직접 fetch + readability** (Jina 실패 시)
   ```bash
   python3 scripts/fetch_url.py "{URL}"
   ```

3. **Playwright** (JavaScript 필요 시)
   ```bash
   python3 scripts/fetch_url.py "{URL}" --js
   ```

4. **yt-dlp** (YouTube/미디어 사이트)
   ```bash
   yt-dlp --write-auto-sub --skip-download "{URL}" -o "inbox/raw/%(title)s"
   ```

### PDF 처리

```bash
python3 scripts/extract_pdf.py "{PDF경로}"
```

추출 순서: pdfplumber → pymupdf → pdfminer

### X.com 스레드 처리

```bash
python3 scripts/fetch_thread.py "{X.com URL}"
```

방법: nitter mirror → Twitter API v2 → 수동 파싱

## 저장 포맷

`clippings/{YYYYMMDD}_{slug}.md`

```yaml
---
source_url: {URL}
source_type: url | pdf | thread | raw | youtube
clipped_at: {ISO8601}
title: {추출 제목}
author: {저자}
word_count: {N}
fetch_method: jina | readability | playwright | yt-dlp | pdf
status: clipped | partial_clip | fetch_failed | raw_saved
domain_hint: ux | trading | pkm | general
tags: []
---

{원문 내용}

---
*원문: [{URL}]({URL})*
```

## 슬러그 생성 규칙

`{YYYYMMDD}_{제목 첫 5단어를 kebab-case로}`

예: `20260507_karpathy-llm-wiki-pattern`

## 도메인 힌트 감지

URL/제목/초반 500자를 분석하여 domain_hint 설정:
- 트레이딩·투자·퀀트·주식·코인·ETF → `trading`
- UX·HCI·사용자·제품·디자인·연구 → `ux`
- 철학·지식·노트·생산성·AI·에세이 → `pkm`
- 기타 → `general`

## 콘텐츠 정제 규칙

추출 후 적용:
- 광고/네비게이션/푸터 제거
- 코드 블록 언어 감지 후 fence 추가 (```python 등)
- 이미지 → alt text 보존 (`![{alt}]({원본 URL})`)
- 수식 → LaTeX fence 보존 (`$$...$$`)
- 표 → Markdown table 변환

## 실패 처리

| 실패 유형 | 처리 |
|----------|------|
| 404/접근차단 | `status: fetch_failed`, URL+timestamp만 저장 |
| 부분 추출 | `status: partial_clip`, 가용 내용 저장 |
| PDF 암호화 | `status: encrypted_pdf`, 파일명+경로 저장 |
| 트위터 비공개 | `status: private_content`, URL만 저장 |

## 스크립트 요구사항

```
scripts/fetch_url.py     — requests + readability-lxml + playwright
scripts/extract_pdf.py   — pdfplumber + pymupdf
scripts/fetch_thread.py  — requests + BeautifulSoup (nitter)
```

의존성 설치:
```bash
pip install requests readability-lxml playwright pdfplumber pymupdf beautifulsoup4
playwright install chromium
```