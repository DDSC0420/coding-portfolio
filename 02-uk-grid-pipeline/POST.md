# LinkedIn post: project 2

**Attach:** `docs/report.png`, or a 30 second recording: run the command in a terminal, the five stages tick through, the report opens.

**Before posting:** the two numbers below are real, from the bundled snapshot (28 Oct to 11 Nov 2026). If you post much later, run it live (`python -m gridwatch run --open`) and update them.

---

My data pipeline's most useful feature is that it refuses to publish.

Project 2 of 8: data engineering.

It pulls half-hourly data on Great Britain's electricity grid, loads it into a database, models it in SQL and writes a one-page report on how clean the grid is and when.

Before the report is written, six checks run on the data. Do the fuel shares add up to 100%? Is every period 30 minutes? Are there gaps? If one fails, the pipeline stops and publishes nothing.

A wrong chart that looks right is worse than no chart.

The rest is the unglamorous part done properly:
→ Run it twice and nothing duplicates
→ Run it tomorrow and it only fetches what's new
→ A flaky API gets retried, a broken one fails loudly
→ 60 tests, none of which need the internet

What the data said for 28 Oct to 11 Nov: the grid averaged 203 gCO₂/kWh, gas supplied 46% of it, and the cleanest hour of the day was 11am.

What it isn't: big data. It's a few thousand rows on a laptop, and the tools are sized for that.

Built with Claude. I set the brief and the bar; the tests back every claim.

Code and a one-minute local setup: https://github.com/DDSC0420/coding-portfolio/tree/main/02-uk-grid-pipeline

#DataEngineering #Python #SQL
