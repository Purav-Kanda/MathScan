# MathScan — Advertising & Promotion Ideas

A running list, starting from the poster idea. Add to this whenever a new idea comes up.

---

## Poster campaign

### Your idea: "Save your Claude usage with this free app"

One flag before printing this as-is, not legal advice, just a real practical risk: naming "Claude" specifically on printed promo material is a trademark issue — it uses another company's product name in your own ad copy, which can read as an implied endorsement or affiliation that doesn't actually exist. Worst case is a takedown request; best case it just looks a little off for something meant to be a clean resume piece.

Same message, same punch, no trademark risk:

- "Stop burning your AI credits on homework photos — do it free here"
- "Free instant handwriting-to-LaTeX. No AI subscription needed."
- "Why pay AI to read your handwriting? This is free."
- "Skip the paywall. Scan your math for free."

### Where to actually put them

Following from the discovery-is-the-bottleneck point — posters only work where the exact target person already stands:

- Math/engineering building bulletin boards
- Library study rooms, especially near printers and scanners
- Math tutoring center / TA office noticeboards
- Whiteboard walls and hallways in STEM-heavy dorms

### Don't skip the QR code

A poster is a physical-to-digital handoff. Without a QR code pointing straight at the live app URL, most people who'd try it on the spot won't bother typing a URL in later. Put it big, not a corner afterthought.

---

## Other channels worth considering (not posters)

- **Class Discords/GroupMes** — already the strongest channel (your friends sending notes are the first real users). Word of mouth from someone who's already used it beats any poster.
- **Subject-specific subreddits** (r/EngineeringStudents, r/calculus, etc.) — check each community's self-promo rules first, most either ban it outright or require a specific day/format ("Self-Promo Saturday" type threads are common).
- **Short demo clip** — the conversion itself (photo in, clean LaTeX out) is visually satisfying and short-form-video-friendly; worth more than static text for showing what it actually does.

---

## On-page: showing the "saves your AI usage" value prop

The pitch only works if visitors immediately see *why* this beats just photographing the page and asking Claude/ChatGPT. Real numbers to use (from `11_Cost_Analysis_MathScan.md` section 4.2/7 — don't invent different ones, these were actually computed):

- Asking Claude to read one photographed page directly costs roughly **$0.004 (Haiku) to $0.038 (Opus)** in API terms per page.
- Over a semester's worth of pages (~192, per the worked example in section 7), that's **~$0.84-$7+** in usage, or a real chunk of a free-tier chat app's daily message cap.
- MathScan's actual cost to run one page is **~$0.0004-$0.0007** (self-hosted Pix2Text on serverless infra) — the free tier can give away pages because each one costs almost nothing, unlike a general-purpose chatbot doing the same task.

Worth being precise about one thing: Pix2Text is still a machine-learning model — "no AI" as a claim would be inaccurate. The honest framing is "no chatbot subscription or message quota used," not "no AI."

Ideas for how to actually show this on the site:

- **A short comparison on the homepage** — a small table or 3-line callout: "Photographing this and asking an AI chatbot: ~$0.01-0.04/page or a chunk of your daily message limit. MathScan: free." Concrete numbers land better than a vague "saves your AI usage" line.
- **Per-conversion feedback** — after a page finishes converting, a small line like "That would've used ~1 chatbot message or a few cents of API cost." Turns an abstract claim into something felt at the moment of actual use, similar to how some apps show "you just saved X" after an action rather than only on a landing page.
- **Lead with the free-tier number, not the savings framing** — "20 free pages a month, no account needed" is a more concrete hook on its own than a comparison claim; the AI-usage comparison works better as supporting detail underneath that, not the headline.

Not yet decided which of these (or which combination) to actually build — this is a real UI/copy task for `web/app/page.tsx`, not documentation-only, whenever it's picked up.

---

## More promotion ideas (beyond posters/Discord/subreddits above)

- **Mention it once while TA'ing** (see chat discussion) — disclose you built it, keep it low-key, don't push the paid pack specifically to your own students, check department policy first.
- **Math tutoring center / TA office** — ask if they'll post the link on their own resource page or noticeboard; a center's endorsement reaches students who wouldn't see a poster.
- **LinkedIn post** — "I built X" posts do double duty here: some real users, and it's the actual internship-portfolio artifact this whole project is partly for. Worth writing even if student-side traffic from it is small.
- **Public GitHub repo + short demo GIF in the README** — recruiters and the "Show HN"/r/SideProject crowd look at repos, not posters. Secondary audience (not students), but reinforces the internship goal directly.
- **CS/math club or ACM chapter listserv**, if your school has one — same idea as class Discords, different room.
- **Campus Piazza/Ed Discussion post**, if the professor's fine with it — same captive audience as TA'ing it live, but opt-in and asynchronous rather than said out loud in front of a class.

---

## Open questions to fill in later

- Actual poster copy/design (once the above phrasing is picked)
- Which specific subreddits/Discords to try first
- Whether a small "made this, would love feedback" post (rather than a pure ad) gets better traction in student communities than a straight promo
- Which on-page savings-display option (if any) to actually build
