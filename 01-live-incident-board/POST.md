# LinkedIn post: project 1

**Attach:** a 30 to 60 second screen recording. Two browser windows side by side, press "Run drill" in one, both move together. Then drag one card in the left window and show it move on the right.

**Before posting:** replace [LIVE LINK] with the deployed URL.

---

Two browser windows. I touch one. The other moves.

This is project 1 of 8 in a series: small, finished, working software, each one a different skill. This one is real-time systems.

It's a live incident board. Everyone on the same link sees every change instantly: new incidents, status changes, updates, who's online.

The part I care about is what happens when two people move the same card at the same moment.

Every incident carries a version number. The first move wins. The second is refused, and that person is shown what really happened. Nothing gets silently overwritten.

Also in there:
→ Drop the connection and it reconnects by itself, then catches up on what it missed
→ 49 automated tests, including two real browsers checked against each other

What it isn't: it runs as one process with state in memory. Scaling it out would need a shared pub/sub layer. It's a demo, and I'd rather say so.

Built with Claude. I set the brief and the bar; the tests back every claim.

Try it (open it in two tabs): [LIVE LINK]
Code: https://github.com/DDSC0420/coding-portfolio/tree/main/01-live-incident-board

#Python #WebSockets #BuildInPublic
