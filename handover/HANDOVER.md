# HANDOVER: "Built with Claude" LinkedIn series

Paste this whole file into a new chat to carry on, or say: "Read handover/HANDOVER.md in DDSC0420/coding-portfolio and carry on." Last updated: 2026-11-11, end of session 2.

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
| 03 | Intake: CV to structured record | AI extraction, validation | **Done.** 126 tests, CI green, scorecard 44/44 |
| 04 | Terraform drift CLI | Packaging, releases | **NEXT** |
| 05 | Subscription SaaS starter | Full-stack, auth, payments | Planned |
| 06 | Chrome extension | Browser platform | Planned |
| 07 | Browser physics game | Graphics | Planned |
| 08 | RELAY workflow engine, finished | Distributed systems | Planned (capstone; an existing separate project of Kyran's, so ask him where its code lives) |

## What exists

- `01-live-incident-board/`: FastAPI + WebSockets, vanilla JS frontend. Rules in `app/rooms.py`, web layer in `app/main.py`. Tests in three layers including two real browser tabs (Playwright).
- `02-uk-grid-pipeline/`: `python -m gridwatch run [--sample]`. Extract (stdlib urllib, windowed, retried) → load (DuckDB, idempotent upserts) → numbered SQL models in `sql/` → six quality checks → self-contained HTML report. `sample/` is a real API snapshot. The workflow has a manual `live` job that runs against the real API and can refresh the sample.
- `03-intake/`: `python -m intake serve|extract|eval`. `readers.py` (type by magic bytes; PDF via pypdf, DOCX incl. page header, list bullets, tables) → `rules.py` (default, no key) or `llm.py` (optional, `INTAKE_LLM=anthropic|groq`, stdlib HTTP) → both return a `Draft` of quoted strings → `validate.py` grounds every value in the CV text (else `rejected`), parses dates/phones/years in code, flags doubtful values `needs_review` with a reason. `schema.py` refuses a value with no evidence. FastAPI page with evidence highlighting. Four invented samples (example.com emails, Ofcom 07700 900xxx numbers) with hand-labelled answers in `samples/expected/`; `scripts/build_samples.py` makes the PDF and DOCX.

## Patterns that worked (reuse them)

- Keep rules in a pure module with no framework imports, and test it directly.
- Playwright browser tests, skipped automatically when Playwright isn't installed; locally run with `PLAYWRIGHT_CHANNEL=chrome`.
- The build sandbox can't reach most external sites. To exercise anything that needs the internet, add a `workflow_dispatch` job and trigger it with `gh api -X POST repos/DDSC0420/coding-portfolio/actions/workflows/<file>/dispatches -f ref=main`.
- Look at the result in a browser (screenshot) before calling UI work done; save the screenshot to `docs/`. A small Playwright script with the sandbox's `/usr/bin/google-chrome` (channel "chrome") works; quantise PNGs with Pillow (256 colours) to keep them around 120 KB.
- Hand-labelled expected answers plus an `eval` command that CI runs (03). Write the answers from the input, never by dumping the tool's output.
- Ground anything an extractor or model returns against its source, and do interpretation (dates, numbers, totals) in code (03).
- Sandbox: Python 3.13, make a venv per project under `/home/claude/` (not in the repo). The `gh` CLI works for REST (`gh api`).

## Open items for Kyran (none block the next build)

- Record a short demo for 01 (two windows side by side, press "Run drill") and post it. Draft: `01-live-incident-board/POST.md`.
- Post 02 with `02-uk-grid-pipeline/docs/report.png`. Draft: `02-uk-grid-pipeline/POST.md`.
- Post 03 with `03-intake/docs/review.png` (or record Rowan's sample: click a review item, watch it light up). Draft: `03-intake/POST.md`; it has an optional line mentioning the agency product, your call.
- 03's LLM mode has only been tested against a fake API. Worth one local run with your own key: `INTAKE_LLM=anthropic ANTHROPIC_API_KEY=... python -m intake eval`. If the default model name (`claude-haiku-5-5`, Groq `llama-3.3-70b-versatile`) is retired, set `INTAKE_LLM_MODEL`.

## Start of next session

Build project 04, **Terraform drift CLI**, to "done". Skill on show: packaging and releases (a real installable tool), using Kyran's Terraform background.

- What it does: reads Terraform plan JSON (`terraform show -json plan.out`) and reports drift: resources changed outside Terraform (`resource_drift`) and pending changes (`resource_changes`), as a table, JSON or Markdown (for a PR comment). Exit codes for CI: 0 no drift, 2 drift, 1 error. Must work with **no cloud credentials and no Terraform installed** on bundled plan JSON fixtures. An optional mode can run `terraform plan -refresh-only` itself.
- Real path, still free: a CI job installs Terraform (`hashicorp/setup-terraform`), applies a tiny config using only the `local` and `random` providers, changes the file outside Terraform, then runs the CLI on a real refresh-only plan and asserts it reports the drift. Use real plan JSON from that job to refresh the fixtures (same idea as 02's `live` job).
- Packaging: `pyproject.toml` with a console entry point, `--version`, CHANGELOG, built wheel and sdist in CI, installed from the wheel into a clean venv and smoke-tested on an OS and Python matrix (ubuntu/macos/windows, 3.10 to 3.13). A tag `drift-vX.Y.Z` triggers a release workflow that attaches the wheel and sdist to a GitHub Release (free). Optionally a composite GitHub Action wrapper so other repos can use it.
- Don't publish to PyPI without asking Kyran (needs his account; Trusted Publishing would be the way). Note it in open items.
- Then update this file and carry straight on to 05.
