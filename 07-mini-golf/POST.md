# LinkedIn post: project 7

**Attach:** `docs/play.png`, or better a 30 to 45 second screen recording of a few holes (the windmill and the island green are the most fun to watch). Optional second image: `docs/scorecard.png`.

---

I built a mini golf game. Then I made the computer prove every hole can be beaten.

Project 7 of 8: graphics and a game loop.

Pocket Links: nine holes in the browser. Sand, ice, a hill, a river with a narrow bridge, bumpers, a windmill, an island green. Drag back and let go, or play it all from the keyboard.

No game engine. The physics is about 300 lines of TypeScript, and it's deterministic: the same shots always end up in exactly the same place, to the last decimal, in the browser and in the tests.

That one property pays for a lot:
→ A solver searches thousands of shots per hole and finds a way round in par. The tests replay those shots on every push, so a change that makes a hole impossible fails the build.
→ The browser test holes hole 1 in one with the keyboard and hole 2 in one with the mouse, using those same stored shots. Same result as in the unit tests, so the browser runs the same physics.
→ The solver also showed me two holes were too easy. So they got harder.

The other part I liked: a fast ball can't pass through a thin wall. Speed is capped so the ball never moves more than its own radius in one step, and a test fires 3,000 full-speed shots at a 1 px wall to check.

What it isn't: tested on phones. Touch works, but it's built for a desktop screen.

Built with Claude. I set the brief and the bar; the tests back every claim.

Code (runs locally with npm install and npm run dev): https://github.com/DDSC0420/coding-portfolio/tree/main/07-mini-golf

#TypeScript #GameDev #WebDevelopment
