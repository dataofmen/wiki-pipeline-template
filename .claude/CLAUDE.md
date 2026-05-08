# {{PROJECT_DIR}} — {{SITE_NAME}} Knowledge Pipeline

## Harness: Information Processing Pipeline

**Goal:** Telegram input -> raw content preservation -> note generation -> Quartz publish (full automation)

**Trigger:** Use the `wiki-orchestrator` skill for pipeline execution, note generation, publishing, wiki updates, or inbox processing requests. Simple questions can be answered directly.

**Structural Principles:**
- `wiki/` — Structured knowledge for AI agent context injection (machine). Use `domain` frontmatter to categorize topics.
- `garden/` — Personal exploration for humans (human). Narrative style, personal perspective.
- `clippings/` -> linked to both `wiki/` and `garden/` via wikilinks (graph visualization).
- Context injection: `python3 scripts/get_context.py --domain <domain>`

**Change History:**
| Date | Change | Target | Reason |
|------|--------|--------|--------|
| 2026-05-07 | Initial setup | All | Information processing pipeline harness created |
| 2026-05-08 | Structure reorganization | Folders, agents, skills | Separated machine (wiki) and human (garden) content principle applied |