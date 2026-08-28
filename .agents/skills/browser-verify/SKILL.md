---
name: browser-verify
description: Verify, reproduce, profile, or screenshot HUD behavior in a real browser.
disable-model-invocation: true
---

# Verify in the browser

Drive the running HUD with playwright from `tests/e2e_temp/`. The directory is
an untracked scratchpad with `@playwright/test` already installed in its own
`node_modules`, so a script runs the moment it is written. The whole value is
a tight loop: write, run, read the verdict, iterate — no installs, no server
management, no new harness.

## The scratchpad contract

- Write scripts and their artifacts (screenshots, videos, JSON) in
  `tests/e2e_temp/`. Everything there is untracked and stays untracked; never
  stage or commit anything from it.
- Name a script after its claim: `<topic>-verify.mjs`, `<topic>-repro.mjs`,
  `<topic>-bench.mjs`. Name artifacts after their script.
- `cd tests/e2e_temp && node <script>.mjs` is the whole invocation.

## Reuse before writing

`ls -t tests/e2e_temp/*.mjs` and read the recent script nearest your task;
start from it. Old scripts are templates, not truths — their selectors and
URLs may be dead. The current ones live in the frontend source and in the
newest scripts.

## Target the live session

- HUD at `http://127.0.0.1:5173`, API at `http://127.0.0.1:5052`. Use the
  user's running session; do not start, stop, or manage servers.
- Everything hot-reloads. After an implementation edit, re-run the script and
  restart nothing. A stale page is a finding, not a nuisance.

## Script discipline

- Take expectations from the API or another authoritative source, never from
  the frontend walk under test.
- Collect `pageerror` and console `error` messages on every page and carry
  them into the verdict. An incidental console error is a finding.
- Wait on observed readiness (for example: file cards present, progress
  indicators gone), not on a fixed sleep alone.
- Print a single JSON verdict at the end — `{report, failures, errors}` — and
  judge the run by that object, not by prose.
- A visual claim requires a screenshot at a desktop viewport (AGENTS.md).
- Re-resolve locators after navigation; element handles held across a reload
  are frozen. State restored from persistence needs a fresh page, not a
  hash-only goto.

## Run modes

- Default: a one-shot `node <script>.mjs` importing `playwright-core`.
- `npx playwright test <name>.spec.mjs` runs specs through
  `playwright.config.mjs` when video recording or the runner's report is
  worth it.
