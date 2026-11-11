# LinkedIn post: project 6

**Attach:** `docs/popup.png`, or a 30 to 45 second recording of the "Try it" steps: select a sentence, Alt+Shift+H, reload (nothing), click Margin (they're back), then the "Show automatically" switch and Chrome's prompt.

---

You highlight a sentence. A week later the page has been edited. Is your highlight still there?

Project 6 of 8: a Chrome extension.

Margin: select text, press a shortcut, it's highlighted. Come back and it's highlighted again, even if the page has changed a little. If it has changed too much to be sure, it says "not found" instead of highlighting the wrong words.

In the picture: three highlights saved, then the article was edited. All three found; the popup flags the two whose wording changed.

The two parts I cared about:
→ Finding it again. It remembers the words plus a little either side, then tries an exact match, a close match, then the words around it. Short passages need their surroundings to agree, so "the moon" doesn't jump to a different "the moon".
→ Permissions done properly. At install it can't read a single website. It looks at a page only when you press the shortcut or click it. "Show automatically" is per site, through Chrome's own prompt, and easy to take back. Nothing leaves your browser.

The tests use the real thing in a real Chrome window: the shortcut pressed like a person presses it, Chrome's permission prompt answered (deny, then allow), the page edited underneath its highlights.

Best moment: every test passed, then the screenshot showed no yellow at all. The highlights existed, but Chrome wasn't drawing them (one stylesheet setting). Now a test checks the actual pixels on screen.

What it isn't: on the Chrome Web Store yet. You load it from the folder in about a minute.

Built with Claude. I set the brief and the bar; the tests back every claim.

Code: https://github.com/DDSC0420/coding-portfolio/tree/main/06-chrome-extension

#ChromeExtension #JavaScript #WebDevelopment
