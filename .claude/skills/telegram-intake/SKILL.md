---
name: telegram-intake
description: 텔레그램 봇 설정 및 운영 스킬. 헤르메스 텔레그램 봇을 설정하고 메시지 수신 규칙을 관리한다. "텔레그램 봇 설정", "봇 시작", "봇 재시작", "헤르메스 봇", "telegram 설정" 요청 시 사용.
---

# Telegram Intake — 텔레그램 봇 스킬

## 봇 설정

### 1. BotFather에서 봇 생성
1. 텔레그램에서 @BotFather 검색
2. `/newbot` 명령 실행
3. 봇 이름: `Hermes` (또는 원하는 이름)
4. 사용자명: `hermes_{{SITE_NAME}}_bot` (또는 유사)
5. 발급된 토큰을 `scripts/.env`에 저장

### 2. 환경 변수 설정

```bash
# scripts/.env
TELEGRAM_BOT_TOKEN="{BotFather 발급 토큰}"
TELEGRAM_ALLOWED_USERS="{허용할 텔레그램 user_id (쉼표 구분)}"
INBOX_DIR="{{PROJECT_DIR}}/inbox"
```

### 3. 봇 데몬 시작

```bash
cd {{PROJECT_DIR}}
python3 scripts/hermes_bot.py &
```

백그라운드 실행. 로그: `scripts/hermes_bot.log`

## 지원 명령어 (봇 내)

| 명령 | 동작 |
|------|------|
| `/start` | 봇 소개 + 사용법 |
| `/status` | 현재 inbox 파일 수, 마지막 처리 시간 |
| `/process` | 즉시 파이프라인 실행 트리거 |
| `/pending` | 미처리 파일 목록 |

## 메시지 처리 규칙

봇이 받을 수 있는 입력:
- **텍스트 메시지**: URL 또는 본문 텍스트
- **파일 첨부**: PDF 문서
- **URL만**: 웹페이지 클리핑
- **Caption 포함 파일**: PDF + 메모

허용 사용자 이외의 메시지는 조용히 무시 (보안).

## 자동 파이프라인 트리거

파일 저장 후 자동으로 파이프라인을 실행하려면:

```bash
# scripts/inbox_watcher.py 별도 실행
python3 scripts/inbox_watcher.py &
```

watcher가 inbox/ 변경 감지 → wiki-orchestrator 자동 실행.

## 봇 재시작

```bash
pkill -f hermes_bot.py
python3 scripts/hermes_bot.py &
```

## launchd로 상시 실행 (macOS)

Mac Mini 재시작 후에도 자동 시작하려면 아래 plist를 `~/Library/LaunchAgents/com.{{SITE_NAME}}.hermes.plist`에 저장:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.{{SITE_NAME}}.hermes</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>{{PROJECT_DIR}}/scripts/hermes_bot.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{{PROJECT_DIR}}/scripts/hermes_bot.log</string>
  <key>StandardErrorPath</key><string>{{PROJECT_DIR}}/scripts/hermes_bot.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.{{SITE_NAME}}.hermes.plist
```