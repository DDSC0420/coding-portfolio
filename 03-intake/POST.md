# LinkedIn post: project 3

**Attach:** `docs/review.png`, or a 30 to 45 second recording: press "Rowan · messy text", click "Ends before it starts" in the review list, and watch the job light up in the CV on the right. Then press "Amara · PDF" and show "Ready to import".

**Optional line, your call:** if you want to signal the agency product, add after the first paragraph: "It's the core of a CV intake tool I'm building for recruitment agencies."

---

A CV parser that shows its working.

Project 3 of 8: AI extraction and validation.

Drop in a CV (PDF, Word or text) and you get back a clean candidate record: contact details, jobs and dates, education, skills.

The part I care about: every value points back to the exact words in the CV it came from. Click any field and it lights up in the original.

If something isn't in the CV, it doesn't go in the record. Nothing gets invented.

That matters most when an LLM is involved. There's an optional mode that uses one, and its output goes through the same checks. In the tests, the model "adds" a PhD and a skill that aren't on the CV. Both get thrown out and listed.

The model only points at text. Dates, phone numbers and years of experience are worked out by plain code.

When something looks wrong, it flags it instead of guessing:
→ A phone number with 9 digits: kept, flagged
→ A job that ends before it starts: flagged, left out of the years total
→ A role with no employer: flagged, not filled in

The default mode needs no API key at all. 126 tests, plus a scorecard against hand-labelled answers.

What it isn't: battle-tested. It's tuned on four invented CVs, and real ones are far messier. The scorecard is a regression check, not an accuracy claim.

Built with Claude. I set the brief and the bar; the tests back every claim.

Code and a one-minute local setup: https://github.com/DDSC0420/coding-portfolio/tree/main/03-intake

#Python #AI #Recruitment
