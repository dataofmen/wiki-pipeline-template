import { QuartzTransformerPlugin } from "../types"
import { Root } from "mdast"
import { visit, SKIP } from "unist-util-visit"

// Convert Obsidian inline footnotes (^[path]) to HTML footnote references
// with a footnotes section at page bottom. Same source within a page collapses
// to one footnote. Example:
//   ^[../0_capture/clippings/2026-04-27-foo.md]
//     -> <sup>1</sup> with link to ../../clippings/2026-04-27-foo
//
// Uses raw HTML nodes instead of mdast footnoteReference/footnoteDefinition
// because Quartz's rehype pipeline does not render those mdast node types.
//
// CrawlLinks (relative strategy) resolves bare "clippings/x" as
// "./clippings/x" relative to the current page's directory, causing 404s.
// To work around this, we prepend the correct "../" prefix based on the
// current page's depth so the path resolves from the vault root.

function pathToSlug(src: string): string {
  let s = src.trim()
  s = s.replace(/^\.\.\//, "")
  s = s.replace(/^\/?0_capture\//, "")
  s = s.replace(/\.md$/i, "")
  s = s.replace(/\s+/g, "-")
  return s
}

function relativeToRoot(slug: string): string {
  const parts = slug.split("/")
  const depth = parts.length - 1
  if (depth <= 0) return "./"
  return "../".repeat(depth)
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
}

export const InlineFootnotes: QuartzTransformerPlugin = () => ({
  name: "InlineFootnotes",
  markdownPlugins() {
    return [
      () => {
        return (tree: Root, file: any) => {
          const currentSlug: string = file.data?.slug || ""
          const prefix = relativeToRoot(currentSlug)
          const sources = new Map<string, { id: string; num: number; src: string }>()
          const seen = new Set<string>()
          let counter = 0

          visit(tree, "text", (node: any, index, parent: any) => {
            if (parent == null || index == null) return
            const text: string = node.value
            if (!text.includes("^[")) return

            const re = /\^\[([^\]]+)\]/g
            const parts: any[] = []
            let last = 0
            let m: RegExpExecArray | null
            while ((m = re.exec(text)) !== null) {
              const [full, src] = m
              if (m.index > last) {
                parts.push({ type: "text", value: text.slice(last, m.index) })
              }

              let entry = sources.get(src)
              if (!entry) {
                counter += 1
                entry = { id: `src-${counter}`, num: counter, src }
                sources.set(src, entry)
              }

              const isFirst = !seen.has(entry.id)
              seen.add(entry.id)

              if (isFirst) {
                parts.push({
                  type: "html",
                  value: `<sup class="footnote-ref"><a href="#fn-${entry.id}" id="fnref-${entry.id}">${entry.num}</a></sup>`,
                })
              } else {
                parts.push({
                  type: "html",
                  value: `<sup class="footnote-ref"><a href="#fn-${entry.id}">${entry.num}</a></sup>`,
                })
              }
              last = m.index + full.length
            }
            if (last < text.length) {
              parts.push({ type: "text", value: text.slice(last) })
            }

            parent.children.splice(index, 1, ...parts)
            return [SKIP, index + parts.length]
          })

          if (sources.size > 0) {
            const items: string[] = []
            for (const [, entry] of sources) {
              const url = "/" + pathToSlug(entry.src)
              const display = esc(entry.src.replace(/^\.\.\//, "").replace(/^0_capture\/clippings\//, ""))
              items.push(`<li id="fn-${entry.id}"><p><a href="${url}" class="internal alias">${display}</a> <a href="#fnref-${entry.id}" class="footnote-backref">↩</a></p></li>`)
            }
            tree.children.push({
              type: "html",
              value: `<section class="footnotes"><ol>\n${items.join("\n")}\n</ol></section>`,
            })
          }
        }
      },
    ]
  },
})