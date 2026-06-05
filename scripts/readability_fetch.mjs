/**
 * Readability.js 기반 본문 추출기
 * Usage: bun scripts/readability_fetch.mjs "https://example.com"
 * Output: JSON { title, content, method, byline?, excerpt?, error? }
 */
import { Readability } from "@mozilla/readability";
import { JSDOM } from "jsdom";
import TurndownService from "turndown";

const url = process.argv[2];
if (!url) {
  console.log(JSON.stringify({ error: "URL 인수 없음", title: "", content: "" }));
  process.exit(1);
}

try {
  const resp = await fetch(url, {
    headers: {
      "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    },
    redirect: "follow",
  });

  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }

  const html = await resp.text();
  if (!html || html.length < 100) {
    throw new Error("빈 응답");
  }

  const dom = new JSDOM(html, { url });
  const reader = new Readability(dom.window.document, {
    charThreshold: 100,
    keepClasses: false,
  });
  const article = reader.parse();

  if (!article || !article.content) {
    throw new Error("본문 추출 실패 — Readability가 기사를 인식하지 못함");
  }

  const td = new TurndownService({
    headingStyle: "atx",
    bulletListMarker: "-",
    codeBlockStyle: "fenced",
    hr: "---",
  });

  // 이미지 alt 텍스트만 유지 (base64 src 제거)
  td.addRule("images", {
    filter: "img",
    replacement: (_, node) => {
      const alt = node.getAttribute("alt") || "";
      const src = node.getAttribute("src") || "";
      if (src.startsWith("data:")) return alt ? `[이미지: ${alt}]` : "";
      return alt ? `![${alt}](${src})` : "";
    },
  });

  const markdown = td.turndown(article.content);

  console.log(JSON.stringify({
    title: article.title?.trim() || "",
    content: markdown,
    method: "readability-js",
    byline: article.byline?.trim() || "",
    excerpt: article.excerpt?.trim() || "",
    lang: article.lang || "",
    siteName: article.siteName || "",
  }));
} catch (e) {
  console.log(JSON.stringify({ error: String(e), title: "", content: "" }));
  process.exit(1);
}
