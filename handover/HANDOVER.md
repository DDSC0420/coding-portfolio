# HANDOVER: "Built with Claude" LinkedIn series

Paste this whole file into a new chat to carry on, or say: "Read handover/HANDOVER.md in DDSC0420/coding-portfolio and carry on." Last updated: 2026-11-11, end of session 1.

## Goal

Eight small, finished, working projects, each a different skill, one LinkedIn post each. Purpose: show hiring managers and clients that Kyran can ship almost anything quickly with Claude.

## Rules

- Repo: `DDSC0420/coding-portfolio` (public). One folder per project, named `NN-project-name`.
- **Local only. Nothing is deployed or hosted** (Kyran's decision, 2026-11-11: hosting costs money and may not be worth it yet). Every project must run on someone's own machine from a clean clone with a couple of commands. No paid services, no required API keys.
- "Done" means all five: runs locally from the README commands · folder with README · tests passing in CI · a screenshot in `docs/` (Kyran records video himself) · LinkedIn post drafted as `POST.md` in the project folder.
- Every README has a "Try it" section near the top and an "Honest scope" section. No production-ready claims.
- Posts: short, one idea, always a visual, say what Claude did and what Kyran did. Only claim what a test backs.
- Each project gets its own CI workflow in `.github/workflows/`, filtered to its folder. Confirm it goes green before calling the project done (`gh api repos/DDSC0420/coding-portfolio/actions/runs`; GraphQL-based `gh` commands don't work in these sessions).
- Don't wait on Kyran between projects: build, verify, push, move to the next.
- At the end of every session: update this file and the status table in the root `README.md`, and push.

## Lineup and status

| # | Project | Skill | Status |
| --- | --- | --- | --- |
| 01 | Live incident board | Real-time, WebSockets | **Done.** 49 tests, CI green |
| 02 | UK grid data pipeline | Data engineering, SQL | **Done.** 60 tests, CI green, live API run verified in CI |
| 03 | Intake: CV to structured record | AI extraction, validation | **NEXT** |
| 04 | Terraform drift CLI | Packaging, releases | Planned |
| 05 | Subscription SaaS starter | Full-stack, auth, payments | Planned |
| 06 | Chrome extension | Browser platform | Planned |
| 07 | Browser physics game | Graphics | Planned |
| 08 | RELAY workflow engine, finished | Distributed systems | Planned (capstone; an existing separate project of Kyran's, so ask him where its code lives) |

## What exists

- `01-live-incident-board/`: FastAPI + WebSockets, vanilla JS frontend. Rules in `app/rooms.py`, web layer in `app/main.py`. Tests in three layers including two real browser tabs (Playwright).
- `02-uk-grid-pipeline/`: `python -m gridwatch run [--sample]`. Extract (stdlib urllib, windowed, retried) → load (DuckDB, idempotent upserts) → numbered SQL models in `sql/` → six quality checks → self-contained HTML report. `sample/` is a real API snapshot. The workflow has a manual `live` job that runs against the real API and can refresh the sample.

## Patterns that worked (reuse them)

- Keep rules in a pure module with no framework imports, and test it directly.
- Playwright browser tests, skipped automatically when Playwright isn't installed; locally run with `PLAYWRIGHT_CHANNEL=chrome`.
- The build sandbox can't reach most external sites. To exercise anything that needs the internet, add a `workflow_dispatch` job and trigger it with `gh api -X POST repos/DDSC0420/coding-portfolio/actions/workflows/<file>/dispatches -f ref=main`.
- Look at the result in a browser (screenshot) before calling UI work done; save the screenshot to `docs/`.

## Open items for Kyran (none block the next build)

- Record a short demo for 01 (two windows side by side, press "Run drill") and post it. Draft: `01-live-incident-board/POST.md`.
- Post 02 with `02-uk-grid-pipeline/docs/report.png`. Draft: `02-uk-grid-pipeline/POST.md`.

## Start of next session

Build project 03, **Intake**, to "done". It doubles as a demo of the CV-intake tool Kyran plans to sell to recruitment agencies.

- Input: a CV (PDF, DOCX or text). Output: a clean, validated, structured candidate record (JSON), plus a simple local page to drop a CV on and see the result.
- It must work with **no API key**: a deterministic rule-based extractor is the default path and the one CI tests. An LLM path (Anthropic or Groq) is optional, switched on by an environment variable, and its output goes through the same validation.
- Show the care: schema validation (Pydantic), confidence or "needs review" flags per field, nothing invented when a field is missing.
- Sample CVs must be invented people, clearly fictional. Never use a real person's CV.
- Then update this file and carry straight on to 04.
