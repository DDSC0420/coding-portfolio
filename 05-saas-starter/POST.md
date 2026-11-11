# LinkedIn post: project 5

**Attach:** `docs/billing.png`, or a 45 to 60 second recording of the "Try it" steps: hit the free limit, upgrade through the test checkout, press "Renew: payment fails" and show the banner, then "Replay last webhook" and show it ignored.

---

Taking someone's money every month is the easy bit. The hard bit is everything after.

Project 5 of 8: a subscription SaaS, end to end.

The product behind the paywall is deliberately tiny. The point is the machinery:
→ Accounts done properly: hashed passwords, real sign-out, CSRF on every form, rate-limited sign-in
→ Plans with limits the server enforces, not just the page
→ Checkout, renewals, a failed payment (you keep access while it's retried), cancel at period end
→ Team plans with seats and invite links

The billing state comes from webhooks, so that's where the care goes. Every event's signature is checked before it's read. A captured request replayed later is rejected. The same event twice is applied once. Events arriving out of order don't undo newer ones.

It all runs on your laptop with no payment account: a built-in test provider plays the part of Stripe, with a test checkout and Stripe-format signed events.

131 tests, including the whole journey in a real browser.

What it isn't: wired to a real payment provider yet. It speaks Stripe's webhook format, but it has only run against the built-in test one.

Built with Claude. I set the brief and the bar; the tests back every claim.

Code and a one-minute local setup: https://github.com/DDSC0420/coding-portfolio/tree/main/05-saas-starter

#SaaS #Python #WebDevelopment
