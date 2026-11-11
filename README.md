# Coding portfolio

Eight small, finished, working projects, each showing a different skill. Every one has a live demo, tests that run in CI, and a plain statement of what it does and doesn't do.

Built with heavy AI assistance (Claude). I set the brief, review the design and hold the bar; the tests back the claims.

| # | Project | Skill it shows | Status |
| --- | --- | --- | --- |
| 01 | [Live incident board](01-live-incident-board/) | Real-time systems, WebSockets | Built and tested; live link pending |
| 02 | UK energy data pipeline | Data engineering, SQL | Planned |
| 03 | Intake: CV to structured record | AI extraction, validation | Planned |
| 04 | Terraform drift CLI | Packaging, releases, CI | Planned |
| 05 | Subscription SaaS starter | Full-stack, auth, payments | Planned |
| 06 | Chrome extension | Browser platform | Planned |
| 07 | Browser physics game | Graphics, game loop | Planned |
| 08 | RELAY workflow engine | Distributed systems | Planned |

## What "finished" means here

A project only counts when it has all five:

1. A live link anyone can open
2. Its own folder here with a README
3. Tests passing in CI
4. A short screen recording
5. A write-up of what it is and isn't

## Layout

- `NN-project-name/`: one self-contained project per folder
- `handover/`: working notes that carry context from one build session to the next
- `render.yaml`: free-tier deploy config for the projects that need a server
