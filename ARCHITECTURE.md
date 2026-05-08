# Architecture

## Three-Space Content Model

The wiki organizes knowledge into three interconnected spaces:

```
                    ┌─────────────┐
                    │  clippings/ │  Raw source material
                    │  (original) │
                    └──────┬──────┘
                           │ wikilinks
                    ┌──────┴──────┐
              ┌─────┴─────┐ ┌─────┴─────┐
              │  wiki/    │ │  garden/  │
              │ (machine) │ │  (human)  │
              └───────────┘ └───────────┘
```

### wiki/ — Machine-Readable Knowledge

- Structured notes with `domain` frontmatter tags
- Designed for AI agent context injection
- Organized by type: `concepts/`, `entities/`
- Example frontmatter:
  ```yaml
  title: Knowledge Graph
  domain: pkm
  slug: knowledge-graph
  tags: [pkm, graph, knowledge-management]
  ```

### garden/ — Human-Readable Insights

- Narrative, personal perspective notes
- Atomic: one idea per note
- Connected via `[[wikilinks]]` to wiki and clippings
- Example frontmatter:
  ```yaml
  title: Capture Friction Kills Knowledge Systems
  slug: capture-friction-kills-knowledge-systems
  tags: [pkm, friction, habits]
  ```

### clippings/ — Raw Source Material

- Original text extracted from URLs, PDFs, threads
- Preserved as-is for reference
- Linked from wiki/garden notes via `[[clippings/slug]]`
- Example frontmatter:
  ```yaml
  title: "Article Title"
  source: "https://example.com/article"
  type: article
  domain: pkm
  ```

## Agent Pipeline

```
Phase 0: Context Check
    ↓
Phase 2: Hermes (Telegram → inbox/)
    ↓
Phase 3: Ingestor (inbox/ → clippings/)
    ↓
Phase 4: Note Maker (clippings/ → wiki/ + garden/)
    ↓
Phase 5: Publisher (wiki/ + garden/ → Quartz build → GitHub Pages)
    ↓
Phase 6: Report & Cleanup
```

### Inter-Agent Communication

Agents communicate via JSONL files in `_workspace/`:

```
_workspace/
├── hermes_new_files.jsonl      # Hermes → Ingestor
├── ingestor_done.jsonl          # Ingestor → Note Maker
├── note_maker_done.jsonl        # Note Maker → Publisher
└── publisher_done.json          # Publisher → Report
```

Each agent reads the previous agent's output file and writes its own completion file. This enables:
- **Partial re-execution**: Run any single phase independently
- **Error recovery**: Failed items are logged, successful items continue
- **Audit trail**: All handshake files are preserved

## 6Rs Note Generation Pipeline

The note-maker applies six stages to each clipping:

| Stage | Action | Output |
|-------|--------|--------|
| R1 Reduce | Extract key insights | Bullet-point insights |
| R2 Reflect | Find connections to existing notes | Link candidates |
| R3 Reweave | Update existing notes with new links | Modified existing notes |
| R4 Record | Create new wiki/garden notes | New note files |
| R5 Verify | Validate frontmatter, links, structure | Checklist pass |
| R6 Rethink | Classify domain, assign tags | Final metadata |

## Quartz Site Architecture

```
quartz/
├── quartz.config.ts      # Site configuration (title, locale, plugins)
├── quartz.layout.ts      # Layout (header, sidebar, graph placement)
├── quartz/components/    # Custom React components
│   ├── HomeGraph.tsx     # Full-page graph on index
│   ├── ConditionalRender.tsx
│   ├── RecentNotes.tsx
│   └── NavLinks.tsx
├── quartz/styles/
│   └── custom.scss       # TOC, graph styling
└── .github/workflows/
    ├── deploy.yml         # GitHub Pages deployment
    └── test.yml           # Build validation CI
```

### Key Layout Decisions

- **Index page**: Global graph only (no title, no sidebar)
- **Content pages**: Title + meta + tags + TOC at top, local graph + backlinks at right
- **List pages**: Title + meta only
- **No left sidebar**: Maximizes reading width

## Deployment Flow

```
Content Repo (wiki/, garden/, clippings/)
    │
    ├── git push ──→ GitHub Actions
    │                    │
    │                    ├── Checkout content repo
    │                    ├── Copy to quartz/content/
    │                    ├── npm ci
    │                    ├── npx quartz build
    │                    └── peaceiris/actions-gh-pages
    │                         │
    │                         └──→ username.github.io (main branch)
    │
    └── Manual: npx quartz sync → same flow
```

## Updating the Template

Users who created wikis from this template can pull updates:

```bash
git remote add upstream https://github.com/dataofmen/wiki-pipeline-template.git
git fetch upstream
git merge upstream/main --allow-unrelated-histories
```

Check `TEMPLATE_VERSION` to see if major changes require migration.