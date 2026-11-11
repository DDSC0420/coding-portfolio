# HANDOVER: "Built with Claude" LinkedIn series

Paste this whole file into a new chat to carry on. Last updated: 2026-11-11, end of session 1.

## Goal

Eight small, finished, working projects, each a different skill, one LinkedIn post each. Purpose: show hiring managers and clients that Kyran can ship almost anything quickly with Claude.

## Rules

- Repo: `DDSC0420/coding-portfolio` (public). One folder per project, named `NN-project-name`.
- Free hosting only. No paid services.
- "Done" means all five: live link · folder with README · tests passing in CI · 30 to 60s screen recording · LinkedIn post drafted (saved as `POST.md` in the project folder).
- Every README has an "Honest scope" section. No production-ready claims.
- Posts: short, one idea, always a visual, say what Claude did and what Kyran did.
- Each project gets its own CI workflow in `.github/workflows/`, filtered to its folder.
- At the end of every session: update this file, the status table in the root `README.md`, and push.

## Lineup and status

| # | Project | Skill | Status |
| --- | --- | --- | --- |
| 01 | Live incident board | Real-time, WebSockets | Code done, 49 tests pass locally. Waiting on Kyran: deploy, recording, post |
| 02 | UK energy data pipeline | Data engineering, SQL | **NEXT** |
| 03 | Intake: CV to structured record | AI extraction | Planned (doubles as demo of the recruitment tool Kyran plans to sell) |
| 04 | Terraform drift CLI | Packaging, releases | Planned |
| 05 | Subscription SaaS starter (Stripe test mode) | Full-stack, payments | Planned |
| 06 | Chrome extension | Browser platform | Planned |
| 07 | Browser physics game | Graphics | Planned |
| 08 | RELAY workflow engine, finished | Distributed systems | Planned (capstone; separate existing project) |

## Project 01: what exists

- `01-live-incident-board/`: FastAPI + WebSockets backend, vanilla JS frontend (no build step), Dockerfile.
- `app/rooms.py` holds the rules (pure Python); `app/main.py` is the web layer.
- Tests: `test_rooms.py` (rules), `test_socket.py` (protocol), `test_browser.py` (two real browser tabs via Playwright).
- Deploy: `render.yaml` at repo root, Render free tier, Docker runtime.

## Open items for Kyran (project 01)

1. Check the CI run went green on GitHub (Actions tab). It has not been confirmed from the build session.
2. Deploy: open https://render.com/deploy?repo=https://github.com/DDSC0420/coding-portfolio and sign in with GitHub. The Dockerfile and `render.yaml` have not been tested on Render yet; if the deploy fails, paste the error into the next chat.
3. Put the live URL into `01-live-incident-board/README.md` and `POST.md`.
4. Record the demo (two windows side by side, press "Run drill").
5. Post it.

## Start of next session

1. Ask Kyran whether project 01's CI and deploy worked; fix anything that didn't.
2. Build project 02, UK energy data pipeline, to "done":
   - Pull public GB grid data on a schedule (the National Grid ESO Carbon Intensity API is free and needs no key; confirm it is still available first).
   - Store it, model it in SQL, chart it on a static page that can be hosted free (GitHub Pages plus a scheduled GitHub Action is the likely shape).
   - Tests for the transform logic; CI workflow; README with honest scope; `POST.md`.
3. Update this file and push.
