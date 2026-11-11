# HANDOVER: "Built with Claude" LinkedIn series

Paste this whole file into a new chat to carry on, or say: "Read handover/HANDOVER.md in DDSC0420/coding-portfolio and carry on." Last updated: 2026-11-11, session 3 (projects 04 and 05 done).

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
| 04 | plandrift: Terraform drift CLI | Packaging, releases | **Done.** 178 tests, all 20 CI jobs green (12-cell OS × Python wheel matrix, real Terraform 1.9.8 / 1.17.1 / OpenTofu e2e, Action on 3 OSes). First release waits on Kyran's click (open items) |
| 05 | Tally: subscription SaaS starter | Full-stack, auth, payments | **Done.** 131 tests (52 rules, 78 app, 1 browser journey), CI green |
| 06 | Chrome extension | Browser platform | **NEXT** (plan below) |
| 07 | Browser physics game | Graphics | Planned |
| 08 | RELAY workflow engine, finished | Distributed systems | Planned (capstone; an existing separate project of Kyran's, so ask him where its code lives) |

## What exists

- `01-live-incident-board/`: FastAPI + WebSockets, vanilla JS frontend. Rules in `app/rooms.py`, web layer in `app/main.py`. Tests in three layers including two real browser tabs (Playwright).
- `02-uk-grid-pipeline/`: `python -m gridwatch run [--sample]`. Extract (stdlib urllib, windowed, retried) → load (DuckDB, idempotent upserts) → numbered SQL models in `sql/` → six quality checks → self-contained HTML report. `sample/` is a real API snapshot. The workflow has a manual `live` job that runs against the real API and can refresh the sample.
- `03-intake/`: `python -m intake serve|extract|eval`. `readers.py` (type by magic bytes; PDF via pypdf, DOCX incl. page header, list bullets, tables) → `rules.py` (default, no key) or `llm.py` (optional, `INTAKE_LLM=anthropic|groq`, stdlib HTTP) → both return a `Draft` of quoted strings → `validate.py` grounds every value in the CV text (else `rejected`), parses dates/phones/years in code, flags doubtful values `needs_review` with a reason. `schema.py` refuses a value with no evidence. FastAPI page with evidence highlighting. Four invented samples with hand-labelled answers in `samples/expected/`.
- `04-terraform-drift/`: package `plandrift` (the name `tfdrift` is taken on PyPI), src layout, hatchling, zero runtime deps, Python 3.10+. `plandrift check PLAN_JSON|-` and `plandrift run [--full-plan] [-- plan args]`; formats table/markdown/json; exit 0/2/1 (argparse usage errors forced to 1); `--fail-on any|drift|never`; `--ignore ADDRESS[:ATTR]` with a bracket-safe glob. Modules: `plan.py` (actions → one word, refuses state JSON / binary plans / errored plans) → `diff.py` (leaf-level diffs, applies sensitive and unknown marks so secrets never leave it) → `report.py` (ignore rules, exit code) → `render.py`. `examples/` has three hand-written AWS-scenario plans (a planted secret `DO-NOT-PRINT` that tests prove never prints) and `examples/real/` with real Terraform 1.17.1 plans committed by CI. `e2e/` applies local + random + Consul (CI service container) resources, tampers with them, and checks the report. `action.yml` is a composite GitHub Action (installs from its own folder into a venv). Workflows: `terraform-drift.yml` (test, build + twine + sdist + git-URL install, smoke ×12, e2e ×3, action ×3, manual `snapshot`) and `terraform-drift-release.yml` (tag `drift-v*` or Run workflow → checks version/changelog/not-already-released → tests → build → `gh release create` → install from the release URL on 3 OSes). `scripts/screenshot.py` makes `docs/terminal.png` from real coloured output.

- `05-saas-starter/`: package `tally`, `python -m tally serve [--open] [--reset] [--no-dev]` (data in `.data/tally.db`). FastAPI + Jinja2 server-rendered pages, no JS, strict CSP. Pure rule modules: `security.py` (scrypt, token digests, signed values, double-submit CSRF, `RateLimiter`), `plans.py` (Free 3 projects / Pro export / Team 5 seats, `effective_plan`, checks), `subscriptions.py` (state machine from Stripe-format events: out-of-order, other-subscription, unknown types ignored), `webhooks.py` (Stripe `t=,v1=` scheme, 300 s window). `store.py` has every query; `migrations/001_init.sql` (PRAGMA user_version). `billing/fake.py` is the test provider: own `fake_*` tables, test checkout page (yellow TEST MODE banner, its own look, never a real provider's), renewals that succeed/fail, ends after 2 failures, replay; it delivers signed events in-process through the same `handle_webhook` as `POST /webhooks/billing`. Workspaces are the billing unit; invites, seats, owner-only billing. Dev panel moves a *billing* clock (sessions use wall time). `scripts/screenshot.py` drives the real app for `docs/billing.png` and `docs/checkout.png`. No real Stripe adapter (dropped from the plan; Honest scope says so).

## Patterns that worked (reuse them)

- Keep rules in a pure module with no framework imports, and test it directly.
- Playwright browser tests, skipped automatically when Playwright isn't installed; locally run with `PLAYWRIGHT_CHANNEL=chrome`.
- The build sandbox can't reach most external sites (e.g. releases.hashicorp.com is blocked; PyPI and GitHub work). To exercise anything that needs the internet or a real tool, do it in a CI job. A `workflow_dispatch` input like `snapshot=true` can commit real outputs back as fixtures (02, 04); `git pull --rebase` afterwards. Trigger with `gh api -X POST repos/DDSC0420/coding-portfolio/actions/workflows/<file>/dispatches -f ref=main -f 'inputs[snapshot]=true'`.
- Look at the result in a browser (screenshot) before calling UI work done; save it to `docs/`. A small Playwright script with the sandbox's `/usr/bin/google-chrome` (channel "chrome") works; quantise PNGs with Pillow (256 colours). For a CLI, render the real ANSI output into a terminal-styled HTML page (04's `scripts/screenshot.py`) and screenshot the window element, not the body.
- Hand-labelled expected answers written from the scenario, never by dumping the tool's output (03, 04's `test_examples.py`, `test_real_plans.py`, `e2e/check_report.py`).
- Ground anything an extractor or model returns against its source, and do interpretation in code (03).
- Plant a known secret in fixtures and assert it never appears in any output (04).
- GitHub Actions gotchas met in 04: a job-level `defaults.run` replaces the workflow's whole `defaults.run`, shell included (Windows then falls back to PowerShell). On Windows runners use `python`, not `python3`; strip `\r` from command output (`tr -d '\r'`); never write `$GITHUB_OUTPUT` lines from Python print. `hashicorp/setup-terraform` needs `terraform_wrapper: false` or it mangles `terraform show -json`. `! cmd` never trips `set -e`.
- FastAPI: don't put `from __future__ import annotations` in a module whose dependencies are functions defined inside the app factory (`Annotated[Ctx, Depends(require_user)]`): the string annotations can't be resolved and every route 422s. Register the error page for Starlette's `HTTPException` so routing 404s get it too.
- A strict CSP (`style-src 'self'`) also blocks inline `style=` attributes: use elements like `<progress>` instead. Any message carried in a URL and shown on the page should be signed (05's `result_page`).
- A dev "time travel" clock should move only the thing being simulated (05: billing time), or it expires sessions mid-demo.
- **This session's git proxy refuses tag pushes (HTTP 403, a policy denial: don't retry or route around it).** Branch pushes to `main` work. So releases get a `workflow_dispatch` path that Kyran starts.
- WebFetch of docs pages may need Kyran's approval (unattended it fails); WebSearch works.
- Sandbox: Python 3.13, make a venv per project under `/home/claude/` (not in the repo). The `gh` CLI works for REST (`gh api`).

## Open items for Kyran (none block the next build)

- **Cut the first plandrift release:** GitHub → Actions → terraform-drift-release → Run workflow (branch main). It tags `drift-v0.1.0`, attaches the wheel and sdist to a Release, and installs it back on Linux/macOS/Windows; check all four jobs go green. (I couldn't push the tag: the session's proxy blocks tag pushes.) After that the README's Releases link has something in it, and `POST.md`'s release line is true.
- Post 05 with `05-saas-starter/docs/billing.png` (or record the "Try it" steps). Draft: `05-saas-starter/POST.md`.
- Post 04 with `04-terraform-drift/docs/terminal.png`. Draft: `04-terraform-drift/POST.md`. Optional second image: an e2e job's Summary page on GitHub, which shows the Markdown (PR-comment) report.
- **Pick a licence for the repo** (none yet, so legally nobody may reuse the code). MIT is the usual choice for a portfolio; it matters most for 04, which is offered as a tool and a GitHub Action. Add `LICENSE` at the root and `license = "MIT"` in `04-terraform-drift/pyproject.toml`.
- PyPI for plandrift: only if you want it. Needs your PyPI account; Trusted Publishing from the release workflow would be the way. The name `plandrift` was free on 2026-11-11.
- Chore, not urgent: GitHub warns that `actions/checkout@v4` and `actions/setup-python@v5` target Node 20 (they still run). Bumping to the Node 24 majors across all workflows clears it.
- Record a short demo for 01 (two windows side by side, press "Run drill") and post it. Draft: `01-live-incident-board/POST.md`.
- Post 02 with `02-uk-grid-pipeline/docs/report.png`. Draft: `02-uk-grid-pipeline/POST.md`.
- Post 03 with `03-intake/docs/review.png`. Draft: `03-intake/POST.md`; it has an optional line mentioning the agency product, your call.
- 03's LLM mode has only been tested against a fake API. Worth one local run with your own key: `INTAKE_LLM=anthropic ANTHROPIC_API_KEY=... python -m intake eval`. If the default model name is retired, set `INTAKE_LLM_MODEL`.

## Start of next session

Build project 06, **Chrome extension**, to "done", in `06-chrome-extension/`. Skill on show: the browser platform (Manifest V3, service worker, content scripts, permissions, storage, messaging). Local only: installed with "Load unpacked"; no Chrome Web Store listing (it costs a fee and needs Kyran's account: open item).

Suggested idea (Kyran hasn't chosen; pick it unless something better fits): **highlights that stay put**. Select text on any page, save it (context menu or a keyboard shortcut), and it's re-highlighted when you come back, even if the page has changed a little. A popup lists the page's highlights; an "All highlights" page searches them and exports Markdown.

- **Permissions done carefully:** by default only `activeTab`, `contextMenus`, `storage`, `scripting`. Remembering highlights on a site is opt-in per origin through `optional_host_permissions` and `chrome.scripting.registerContentScripts`, so it never reads every page by default. Everything stays in `chrome.storage.local`; nothing leaves the browser. Say so in the README.
- **Anchoring in a pure module:** W3C Web Annotation style: TextQuoteSelector (exact text plus ~32 characters of prefix and suffix) with a TextPositionSelector hint; re-anchor by exact match, then prefix/suffix context, then a bounded fuzzy match; report "couldn't find" rather than highlight the wrong text. Unit-test it with `node --test` on plain strings (no dependencies).
- **No build step:** plain ES modules, so "Load unpacked" on the folder just works. A script zips it for release; CI uploads the zip.
- **Tests:** `node --test` for the pure modules; Playwright loading the unpacked extension in Chromium (persistent context with `--load-extension`; run under `xvfb-run` in CI if headless can't load extensions) on local fixture pages: save a highlight, reload, it's back; edit the page text slightly, still found; change it a lot, reported as lost; popup lists it; export matches. Look at it in a browser and screenshot it.
- Then update this file and the root README, and carry on to 07.
