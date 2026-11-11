# LinkedIn post: project 4

**Attach:** `docs/terminal.png`. Optional second image: the Markdown report on a CI run page (Actions → terraform-drift → any "e2e" job → Summary), which is exactly what a PR comment looks like.

---

Someone "fixed" production in the AWS console on a Friday. Terraform has no idea.

Project 4 of 8: packaging and releases.

plandrift reads a Terraform plan and says in plain terms what was changed outside Terraform, and what applying the plan would do.

In the picture: SSH opened to the whole internet "temporarily", a tag changed, a server deleted, a database scaled up. Exit code 2, so a nightly CI job goes red and someone looks.

The real point of this one: a proper tool, not a script.
→ pip install from a GitHub Release, zero dependencies
→ Tested from the built package on Linux, macOS and Windows, Python 3.10 to 3.13: 12 combinations
→ Push a version tag and CI tests, builds and publishes the release, then installs it back from the release on all three systems
→ Works as a GitHub Action that puts the report on the run page

And checked against the real thing: CI applies a small setup with real Terraform (old and latest) and OpenTofu, changes things behind its back, and checks plandrift reports exactly what changed. No cloud account needed.

Secrets never get printed. Terraform's plan JSON holds them in plain text, so a test plants some and checks none appear in any output.

What it isn't: tested on a real AWS estate. The AWS examples are hand-written in Terraform's plan format; the real runs use free local providers.

Built with Claude. I set the brief and the bar; the tests back every claim.

Code: https://github.com/DDSC0420/coding-portfolio/tree/main/04-terraform-drift

#Terraform #DevOps #Python
