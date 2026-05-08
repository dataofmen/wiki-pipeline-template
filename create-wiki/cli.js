#!/usr/bin/env node

import { intro, outro, text, select, confirm, multiselect, isCancel, cancel, spinner, log } from "@clack/prompts"
import pc from "picocolors"
import { execSync } from "child_process"
import { readFileSync, writeFileSync, readdirSync, mkdirSync, rmSync, existsSync } from "fs"
import { join, resolve, basename } from "path"

const TEMPLATE_REPO = "dataofmen/wiki-pipeline-template"
const VERSION = "1.0.0"

const LOCALE_FONTS = {
  "ko-KR": { header: "Noto Sans KR", body: "Noto Sans KR" },
  "en-US": { header: "Inter", body: "Inter" },
  "ja-JP": { header: "Noto Sans JP", body: "Noto Sans JP" },
  "zh-CN": { header: "Noto Sans SC", body: "Noto Sans SC" },
  "de-DE": { header: "Inter", body: "Inter" },
  "fr-FR": { header: "Inter", body: "Inter" },
}

async function main() {
  console.log()
  intro(pc.bgCyan(pc.black(` create-wiki v${VERSION} `)))

  const siteName = await text({
    message: "What would you like to call your wiki?",
    placeholder: "my-knowledge-base",
    validate: (v) => v.length === 0 ? "Please enter a name" : undefined,
  })
  if (isCancel(siteName)) { cancel("Cancelled."); process.exit(0) }

  const locale = await select({
    message: "Language for the site UI?",
    options: [
      { value: "ko-KR", label: "Korean" },
      { value: "en-US", label: "English" },
      { value: "ja-JP", label: "Japanese" },
      { value: "zh-CN", label: "Chinese (Simplified)" },
      { value: "de-DE", label: "German" },
      { value: "fr-FR", label: "French" },
    ],
  })
  if (isCancel(locale)) { cancel("Cancelled."); process.exit(0) }

  const ghUser = await text({
    message: "GitHub username?",
    placeholder: "your-username",
  })
  if (isCancel(ghUser)) { cancel("Cancelled."); process.exit(0) }

  const repoName = await text({
    message: "Repository name?",
    initialValue: siteName,
  })
  if (isCancel(repoName)) { cancel("Cancelled."); process.exit(0) }

  const deployPages = await confirm({
    message: "Deploy to GitHub Pages?",
    initialValue: true,
  })
  if (isCancel(deployPages)) { cancel("Cancelled."); process.exit(0) }

  const setupTelegram = await confirm({
    message: "Set up Telegram intake bot? (optional)",
    initialValue: false,
  })
  if (isCancel(setupTelegram)) { cancel("Cancelled."); process.exit(0) }

  const includeExamples = await confirm({
    message: "Include example content?",
    initialValue: true,
  })
  if (isCancel(includeExamples)) { cancel("Cancelled."); process.exit(0) }

  const fonts = LOCALE_FONTS[locale] || LOCALE_FONTS["en-US"]
  const baseUrl = deployPages ? `${ghUser}.github.io` : ""

  const config = {
    SITE_NAME: siteName,
    PROJECT_DIR: repoName,
    LOCALE: locale,
    HEADER_FONT: fonts.header,
    BODY_FONT: fonts.body,
    GH_USER: ghUser,
    REPO: repoName,
    BASE_URL: baseUrl,
    BOT_NAME: `hermes_${siteName}_bot`,
    DATE: new Date().toISOString().split("T")[0],
  }

  // Clone template
  const targetDir = resolve(process.cwd(), repoName)
  const s = spinner()
  s.start("Cloning template...")

  try {
    execSync(`npx degit ${TEMPLATE_REPO} "${targetDir}"`, { stdio: "pipe" })
  } catch {
    // Fallback: direct git clone
    try {
      execSync(`git clone https://github.com/${TEMPLATE_REPO}.git "${targetDir}" --depth 1`, { stdio: "pipe" })
      rmSync(join(targetDir, ".git"), { recursive: true })
    } catch (e) {
      s.stop("Failed to clone template")
      log.error(`Could not clone template. Create the repo manually from https://github.com/${TEMPLATE_REPO}`)
      process.exit(1)
    }
  }

  s.stop("Template cloned")

  // Replace placeholders
  const replaceSpinner = spinner()
  replaceSpinner.start("Applying configuration...")

  replaceInDir(targetDir, config)
  replaceSpinner.stop("Configuration applied")

  // Remove Telegram files if not wanted
  if (!setupTelegram) {
    const tgFiles = [
      join(targetDir, "scripts", "hermes_bot.py"),
      join(targetDir, "scripts", "inbox_watcher.py"),
      join(targetDir, ".claude", "skills", "telegram-intake"),
    ]
    tgFiles.forEach((f) => {
      if (existsSync(f)) rmSync(f, { recursive: true, force: true })
    })
  }

  // Remove example content if not wanted
  if (!includeExamples) {
    const examples = [
      join(targetDir, "wiki", "concepts", "example-concept.md"),
      join(targetDir, "garden", "example-insight.md"),
      join(targetDir, "garden", "example-second-insight.md"),
      join(targetDir, "clippings", "example-clipping.md"),
    ]
    examples.forEach((f) => {
      if (existsSync(f)) rmSync(f, { force: true })
    })
  }

  // Install dependencies
  const installSpinner = spinner()
  installSpinner.start("Installing Quartz dependencies...")
  try {
    execSync("npm install", { cwd: join(targetDir, "quartz"), stdio: "pipe" })
    installSpinner.stop("Dependencies installed")
  } catch {
    installSpinner.stop("npm install failed (run manually: cd quartz && npm install)")
  }

  // Git init
  const gitSpinner = spinner()
  gitSpinner.start("Initializing git repository...")
  try {
    execSync("git init", { cwd: targetDir, stdio: "pipe" })
    execSync("git add -A", { cwd: targetDir, stdio: "pipe" })
    execSync('git commit -m "Initial commit: wiki pipeline setup"', { cwd: targetDir, stdio: "pipe" })
    gitSpinner.stop("Git repository initialized")
  } catch {
    gitSpinner.stop("Git init failed (run manually)")
  }

  console.log()
  outro(pc.green("Wiki created!") + pc.dim(` → ${targetDir}`))

  console.log()
  console.log(pc.bold("Next steps:"))
  console.log(pc.cyan(`  cd ${repoName}`))

  if (setupTelegram) {
    console.log(pc.cyan("  cp scripts/.env.example scripts/.env  # Add your bot token"))
    console.log(pc.cyan("  python3 scripts/hermes_bot.py         # Start Telegram bot"))
  }

  console.log(pc.cyan("  cd quartz && npx quartz build --serve  # Preview at localhost:8080"))

  if (deployPages) {
    console.log()
    console.log(pc.bold("GitHub Pages deployment:"))
    console.log(pc.cyan("  gh repo create                       # Create GitHub repo"))
    console.log(pc.cyan("  git remote add origin <repo-url>"))
    console.log(pc.cyan("  git push -u origin main"))
    console.log(pc.cyan("  # Add GH_PAT secret in repo settings"))
  }

  console.log()
}

function replaceInDir(dir, config) {
  const entries = readdirSync(dir, { withFileTypes: true })
  for (const entry of entries) {
    const fullPath = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".git") continue
      replaceInDir(fullPath, config)
    } else if (entry.isFile()) {
      try {
        let content = readFileSync(fullPath, "utf-8")
        let modified = false
        for (const [key, value] of Object.entries(config)) {
          const placeholder = `{{${key}}}`
          if (content.includes(placeholder)) {
            content = content.replaceAll(placeholder, value)
            modified = true
          }
        }
        if (modified) {
          writeFileSync(fullPath, content)
        }
      } catch { /* skip binary files */ }
    }
  }
}

main().catch(console.error)