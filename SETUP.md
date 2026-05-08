# Setup Guide

## Prerequisites

- [Node.js](https://nodejs.org/) 18+ and npm
- [Python](https://python.org/) 3.10+ (for Telegram bot)
- [Claude Code](https://claude.ai/code) CLI
- [Git](https://git-scm.com/) and [GitHub CLI](https://cli.github.com/)

## Step-by-Step Setup

### 1. Create Your Wiki

**Option A: CLI**
```bash
npx create-wiki
```

**Option B: Manual**
1. Use this template on GitHub
2. Clone the repo
3. Run `cd quartz && npm install`

### 2. Configure Your Site

Edit these files to replace template placeholders:

| File | What to change |
|------|---------------|
| `quartz/quartz.config.ts` | `pageTitle`, `baseUrl`, `locale`, fonts |
| `index.md` | Site name, note counts |
| `.claude/CLAUDE.md` | Site name, project directory |

### 3. Set Up Telegram Bot (Optional)

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot: `/newbot`
3. Copy the bot token
4. Get your Telegram user ID: message [@userinfobot](https://t.me/userinfobot)
5. Create `.env` file:
   ```bash
   cp scripts/.env.example scripts/.env
   ```
6. Edit `scripts/.env` with your token and user ID
7. Install Python dependencies:
   ```bash
   pip install pytelegrambotapi python-dotenv watchdog requests readability-lxml
   ```
8. Start the bot:
   ```bash
   python3 scripts/hermes_bot.py
   ```

### 4. Set Up GitHub Pages

1. Create a GitHub repo:
   ```bash
   gh repo create --private
   git remote add origin <your-repo-url>
   git push -u origin main
   ```

2. Add `GH_PAT` secret:
   - Go to repo Settings → Secrets and variables → Actions
   - Add `GH_PAT` with a personal access token (needs `repo` scope)

3. Create your GitHub Pages repo (`<username>.github.io`):
   ```bash
   gh repo create <username>/<username>.github.io --public
   ```

4. Push to trigger deployment:
   ```bash
   git push
   ```

### 5. Preview Locally

```bash
cd quartz
npx quartz build --serve
```

Open http://localhost:8080

### 6. Start Processing Content

**Via Telegram:**
Send a URL to your bot. The pipeline runs automatically.

**Via Manual:**
1. Create a file in `inbox/urls/`:
   ```bash
   echo "https://example.com/article" > inbox/urls/$(date +%Y%m%d)-article.md
   ```
2. Run the pipeline:
   ```bash
   # In Claude Code
   /wiki-orchestrator
   ```

## macOS LaunchAgent (Bot Persistence)

To run the Telegram bot automatically on startup:

```bash
cat > ~/Library/LaunchAgents/com.wiki.hermis-bot.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.wiki.hermes-bot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$(pwd)/scripts/hermes_bot.py</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>$(pwd)</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.wiki.hermes-bot.plist
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Quartz build fails | `cd quartz && rm -rf node_modules && npm install` |
| Telegram bot not responding | Check `.env` token, check bot is running |
| Broken wikilinks | Run `python3 scripts/check_links.py` |
| GitHub Pages 404 | Check `GH_PAT` secret, check workflow ran |
| Graph not showing | Verify `quartz.layout.ts` has HomeGraph component |