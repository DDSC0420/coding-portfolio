# LinkedIn post: project 8 (the capstone)

**Attach:** best is a 40 to 60 second screen recording: the dashboard on the left, a terminal on the right. Send a batch, run `docker compose kill worker-a`, and let the lease bars drain and move to worker-b. Otherwise `docs/img/live.png`, with `docs/img/workflow.png` as a second image.

---

I killed a worker in the middle of a job. Nothing was lost, and nothing was done twice.

Project 8 of 8, the big one: a fault-tolerant workflow engine.

RELAY runs multi-step jobs across several worker processes. Python, FastAPI and PostgreSQL, no Celery and no Temporal, because I wanted to understand what those tools actually do for you.

The questions it had to answer:
→ A worker dies mid-step. Who notices, and who finishes the work?
→ A worker freezes for 30 seconds, wakes up, and still thinks the job is its own. What stops it overwriting the worker that took over?
→ The CRM saves the record, then the reply gets lost. Retry and risk a duplicate, or give up and risk losing it?

How:
→ Workers claim steps from PostgreSQL. Each claim is a lease that has to be renewed.
→ Every claim gets a number that only goes up. A late write with an old number is refused.
→ The CRM call carries a key fixed at the start, so a retry gets the saved answer back instead of a second write.
→ If it truly can't tell whether something happened, it says "outcome unknown" instead of guessing.

229 tests. They kill and freeze real worker processes, drop replies after the save, and restart everything mid-batch. On every push, CI starts the whole thing in Docker, kills a worker, and then checks both databases: one CRM write per job, never two.

My favourite bug wasn't found by a test. A dashboard screenshot showed a perfectly good job marked "failed" because the worker died at the wrong moment. One setting was doing two jobs. Fixed, and there's a test for it now.

What it isn't: exactly-once, or production-ready. It's at-least-once, and the README lists every limit.

Built with heavy AI assistance: Claude wrote the code, from a design written down first, and I can walk you through every decision in it.

That's eight finished projects across eight different skill sets. I'm looking for backend, platform or AI automation roles where reliability matters. If that's your team, I'd like to talk.

Code and a three-minute demo: https://github.com/DDSC0420/coding-portfolio/tree/main/08-relay

#Python #PostgreSQL #DistributedSystems
