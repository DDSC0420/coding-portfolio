# Coding portfolio

Eight small, finished, working projects, each showing a different skill. Every one runs on your own machine with a couple of commands, has tests that run in CI, and a plain statement of what it does and doesn't do.

Built with heavy AI assistance (Claude). I set the brief, review the design and hold the bar; the tests back the claims.

| # | Project | Skill it shows | Status |
| --- | --- | --- | --- |
| 01 | [Live incident board](01-live-incident-board/) | Real-time systems, WebSockets | Done |
| 02 | [UK grid data pipeline](02-uk-grid-pipeline/) | Data engineering, SQL | Done |
| 03 | [Intake: CV to structured record](03-intake/) | AI extraction, validation | Done |
| 04 | [plandrift: Terraform drift report](04-terraform-drift/) | Packaging, releases, CI | Done |
| 05 | [Tally: subscription SaaS starter](05-saas-starter/) | Full-stack, auth, payments | Done |
| 06 | [Margin: highlights that stay put](06-chrome-extension/) | Browser platform, Chrome extensions | Done |
| 07 | [Pocket Links: mini golf](07-mini-golf/) | Graphics, game loop, physics | Done |
| 08 | [RELAY: a workflow engine you can kill mid-step](08-relay/) | Distributed systems, PostgreSQL | Done |

## What "finished" means here

A project only counts when it has all five:

1. Runs locally from a clean clone with the commands in its README
2. Its own folder here with that README
3. Tests passing in CI
4. A screenshot or short recording
5. A write-up of what it is and isn't

## Layout

- `NN-project-name/`: one self-contained project per folder
- `handover/`: working notes that carry context from one build session to the next
