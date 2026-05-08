# Wiki Pipeline Template

**Information processing pipeline: Telegram → Extract → 6Rs Notes → Quartz Wiki**

A complete knowledge management system that transforms raw information into a connected, searchable wiki — automatically.

## What It Does

1. **Capture** — Send URLs, PDFs, or X.com threads via Telegram (or drop files in `inbox/`)
2. **Extract** — Original content is extracted and saved to `clippings/`
3. **Generate** — 6Rs pipeline creates structured notes in `wiki/` and `garden/`
4. **Publish** — Quartz builds a static site with interactive knowledge graph

## Three-Space Content Model

| Space | Purpose | Audience |
|-------|---------|----------|
| `wiki/` | Structured knowledge with domain tags | AI agents (machine) |
| `garden/` | Narrative atomic notes with personal insights | Humans |
| `clippings/` | Raw source material (preserved as-is) | Both (linked via wikilinks) |

## Quick Start

### Option 1: CLI (Recommended)

```bash
npx create-wiki
```

Follow the interactive prompts to configure your wiki.

### Option 2: GitHub Template

1. Click **"Use this template"** on this repository
2. Clone your new repo
3. Configure: replace `{{PLACEHOLDER}}` values in key files
4. Install: `cd quartz && npm install`
5. Preview: `npx quartz build --serve`

## Architecture

```
Telegram ──→ Hermes ──→ Ingestor ──→ Note Maker ──→ Publisher ──→ Quartz Site
  (bot)      (inbox/)   (clippings/)  (wiki/garden)    (sync+build)   (GitHub Pages)
```

### Agent Pipeline

| Agent | Role | Input | Output |
|-------|------|-------|--------|
| Hermes | Telegram gateway | Telegram messages | `inbox/` files |
| Ingestor | Content extraction | `inbox/` | `clippings/` |
| Note Maker | 6Rs note generation | `clippings/` | `wiki/` + `garden/` |
| Publisher | Quartz build + deploy | `wiki/` + `garden/` | Static site |

### 6Rs Note Generation

1. **Reduce** — Extract key insights from clipping
2. **Reflect** — Discover connections to existing notes
3. **Reweave** — Update existing notes with new links
4. **Record** — Create new wiki/garden notes
5. **Verify** — Validate frontmatter, links, structure
6. **Rethink** — Note domain classification and tagging

## Configuration

### Environment Variables (`.env`)

```bash
TELEGRAM_BOT_TOKEN=           # From @BotFather
TELEGRAM_ALLOWED_USERS=       # Comma-separated Telegram user IDs
INBOX_DIR=                    # Default: ../inbox
BOT_NAME=                     # Default: hermes_<site>_bot
CLAUDE_CLI_PATH=               # Default: claude
```

### Key Template Placeholders

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `{{SITE_NAME}}` | Wiki title | my-knowledge-base |
| `{{LOCALE}}` | UI language | ko-KR, en-US |
| `{{GH_USER}}` | GitHub username | your-username |
| `{{REPO}}` | Repository name | my-wiki |
| `{{BASE_URL}}` | Site URL | username.github.io |

## Deployment

### GitHub Pages

1. Add `GH_PAT` secret in your repo settings (personal access token)
2. Push to `v4` branch (or `main` depending on your setup)
3. GitHub Actions builds and deploys automatically

### Manual Build

```bash
cd quartz
npx quartz build          # Build to public/
npx quartz build --serve  # Preview at localhost:8080
npx quartz sync           # Push to GitHub Pages
```

## Customization

### Quartz Components

- `HomeGraph.tsx` — Full-page knowledge graph on index
- `ConditionalRender.tsx` — Show/hide components by page
- `RecentNotes.tsx` — Recent notes with tags
- `NavLinks.tsx` — Custom navigation links

### Styling

- `quartz/styles/custom.scss` — TOC styling, graph display
- `quartz.config.ts` — Fonts, colors, plugins

## Requirements

- **Node.js** 18+ (for Quartz)
- **Python** 3.10+ (for Telegram bot, optional)
- **Claude Code** (for agent pipeline)

## License

MIT