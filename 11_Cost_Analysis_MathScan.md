# Cost Analysis: Self-Hosted OCR vs. Frontier Vision APIs

## Project: MathScan
**Date:** July 2026
**Companion to:** 09_SRS_MathScan.md, 10_SDD_MathScan.md

---

## 1. Why this file exists

MathScan's current pipeline (M0/M1, built and verified) uses Pix2Text-MFR, a small open-source OCR model, self-hosted on our own server. A natural question: general chat products like Claude or ChatGPT can already read a photo of handwritten math and return LaTeX — often with better accuracy on messy handwriting, since they can use broader reasoning. So why not just call one of those APIs directly for every request?

The answer is cost, and it's a bigger gap than it might look at first. This file works out the actual numbers, using each provider's own published pricing.

**The actual product decision (section 5): two tiers, both self-hosted Pix2Text, no Claude involved.** Free — 50 pages/month. Paid — $5/year, up to 500 pages/month. Simple on purpose: no per-page billing, no payment-processor overhead on tiny transactions, no second inference engine to maintain. Sections 4 and 4.4 keep the Claude-routing cost research in the file as background — it's what makes "no Claude tier" a deliberate choice with real numbers behind it, not a decision made without looking.

---

## 2. The architectural difference in one sentence

Self-hosting has a **fixed monthly cost that gets divided across however many pages you process** (cost per page falls as volume rises). Calling a frontier vision API has a **cost that scales roughly linearly with volume** (each page costs about the same regardless of whether you process 10 or 10,000 that month). This one difference drives the entire tier design below.

---

## 3. Self-hosted cost (the free tier, already built)

Per the SDD (section 2.2), the inference VM (Hetzner GEX44 or similar) costs **~$30-50/month**, handling any volume the GPU can keep up with (SDD estimates comfortably serving <100 daily active users on one box).

Once that VM is paid for, running one more page through Pix2Text costs effectively **$0** in direct API fees — just a slice of electricity and GPU-hours you're already paying for. So the real "cost per page" is just the VM's flat fee divided by however many pages actually ran that month:

| Pages processed per month | Cost per page (at $40/month VM) |
|---|---|
| 1,000 | $0.0400 |
| 10,000 | $0.0040 |
| 100,000 | $0.0004 |

This is the whole point of the free tier: it gets *cheaper per page* as usage grows, and it never has a per-request bill from a third party.

### 3.1 Do you actually have to pay $30-50/month even if nobody uses it yet?

No — that fixed VM cost was never a requirement, it was a choice that makes sense once there's real, steady traffic. Before that, a **scale-to-zero serverless GPU** host charges only for the seconds it's actually running inference, and drops to **exactly $0** the moment nobody's using it. That's a much better fit for a small/unproven launch.

Current (2026) per-second rates on a small GPU that's plenty for a lightweight formula-recognition model like Pix2Text-MFR (no need for an H100-class card — that's built for large LLMs, not this):

| Platform | GPU | Rate | Est. cost per page (~4 sec of inference) |
|---|---|---|---|
| Modal | T4 | $0.000164/sec | ~$0.0007 |
| RunPod Serverless | T4 | ~$0.00011/sec | ~$0.0004 |
| Google Cloud Run (GPU) | L4 | billed per second, scales to zero | similar order of magnitude |

*(The 4-second-per-page inference time is a planning assumption, not a measured number — worth timing on your own machine before committing to it. Pix2Text is a small model, so a few seconds on a T4 is a reasonable guess, but verify.)*

**Where this actually wins:** at zero or low traffic, cost is $0 or a few cents a day — no risk of paying for an idle box nobody's hitting. **Where the fixed VM eventually wins:** at high sustained volume, since serverless cost scales with usage while the VM's cost doesn't. The crossover point, using the $0.0007/page (Modal) estimate above:

| Monthly VM cost (the alternative you'd be comparing against) | Pages/month where the fixed VM becomes cheaper than serverless |
|---|---|
| $30 | ~46,000 |
| $40 | ~61,000 |
| $50 | ~76,000 |

**Practical recommendation: start on serverless (Modal or RunPod), pay nothing while validating whether anyone uses this, and only move to a dedicated VM once you're consistently past ~50,000-60,000 pages/month** — a real, demonstrated usage level, not a guess made before launch. This also removes the "pay for a VM nobody uses" risk from the whole M4 deployment step, not just the cost analysis.

**Trade-off, stated plainly:** serverless GPUs have a cold-start delay — the container has to spin up before the first request after idle time is served, adding anywhere from under a second to tens of seconds depending on platform and model size. For a low-traffic launch this is a fine trade (the app's per-page progress UI already sets the expectation that pages take a few seconds), but it's a real UX cost compared to an always-warm dedicated VM.

---

## 4. Frontier vision API cost (background research — not part of the current product plan)

**Note before reading this section:** the actual tier structure (section 5) doesn't route anything to Claude — it's two self-hosted Pix2Text tiers, free and paid. This section stays in the file as the reasoning *why* that's the choice: it's the real cost of the alternative (routing to Claude), worked out with real numbers instead of assumed. Worth keeping for later — if a "Premium, Claude-level accuracy" tier ever gets revisited, this is the starting math for it.

### 4.1 How image costs are calculated (Claude, confirmed from Anthropic's own docs)

Claude converts an image into "visual tokens" using the formula `⌈width/28⌉ × ⌈height/28⌉`, then bills those like any other input token. A realistic full-page phone photo of a homework page, resized to fit Claude's high-resolution tier (long edge up to 2576px, capped at 4,784 visual tokens on Sonnet 5 / Opus 4.8), lands **at or near that 4,784-token cap** — a detailed, full-resolution page photo is large enough to hit the ceiling, not the low end.

### 4.2 Worked cost per page, by model

Assumptions: ~4,784 input tokens per page image (near Claude's high-res cap) + ~300 tokens for our instruction prompt, ~500 output tokens for the returned LaTeX (a full page of a few equations). These are reasonable planning estimates, not guarantees — real pages vary.

| Model | Input rate | Output rate | Est. cost / page | Notes |
|---|---|---|---|---|
| Claude Haiku 4.5 | $1/MTok | $5/MTok | **~$0.0044** | Standard-resolution tier, capped at 1,568 visual tokens — cheaper, but lower image fidelity for dense handwriting |
| Claude Sonnet 5 (through Aug 31, 2026) | $2/MTok | $10/MTok | **~$0.0152** | High-res tier, introductory pricing |
| Claude Sonnet 5 (from Sept 1, 2026) | $3/MTok | $15/MTok | **~$0.0228** | Same model, standard pricing after the intro period ends |
| Claude Opus 4.8 | $5/MTok | $25/MTok | **~$0.0379** | Highest quality, highest cost |
| OpenAI GPT-5.4 (approximate) | $1.25-2.50/MTok | $7.50-15/MTok | **~$0.006-0.013** | OpenAI's exact image-to-token formula wasn't available in what I could pull directly; this range is estimated from comparable per-token rates, not a confirmed image-token count — verify before relying on it for real billing |

### 4.3 Same volumes as the self-hosted table, using Claude Sonnet 5 (recommended paid-tier model)

| Pages processed per month | Cost at ~$0.015/page |
|---|---|
| 1,000 | ~$15 |
| 10,000 | ~$150 |
| 100,000 | ~$1,500 |

Notice this does **not** get cheaper per page as volume grows — it's a real, ongoing per-request bill, unlike the self-hosted flat fee. This is exactly why this can't just be the only tier: giving this away for free at any real scale would lose money predictably and by design, not by accident.

---

## 4.4 Is it cheaper to run Pix2Text first, then send the LaTeX text to Claude, instead of sending the raw image to Claude?

**Yes, noticeably cheaper — but it's a different capability, not just a cheaper version of the same thing.** Worth separating those two facts.

**Why it's cheaper.** Claude's image-token cost comes from pixel count, not from how much math is on the page — a full photo runs ~4,784 visual tokens (Sonnet/Opus) or ~1,568 (Haiku) regardless of content. Pix2Text's raw LaTeX output for a page, by contrast, is just short text — a handful of equations is typically well under 200 tokens. Feeding *that* to Claude as plain text, instead of the photo, cuts the input side of the bill by roughly 10x.

Assumptions: ~150 tokens of raw Pix2Text LaTeX output + ~300 tokens of instruction prompt = 450 input tokens; same ~500 output tokens (a cleaned-up, restructured LaTeX page) as the direct-image estimates above.

| Model | Hybrid (Pix2Text text → Claude) | Direct image → Claude | Hybrid is cheaper by |
|---|---|---|---|
| Claude Haiku 4.5 | **~$0.0029** | ~$0.0044 | ~1.5x |
| Claude Sonnet 5 (intro pricing) | **~$0.0059** | ~$0.0152 | ~2.6x |
| Claude Sonnet 5 (post-Sept 2026) | **~$0.0089** | ~$0.0228 | ~2.6x |
| Claude Opus 4.8 | **~$0.0148** | ~$0.0379 | ~2.6x |

Pix2Text's own inference cost doesn't change either way (still $0 marginal, same self-hosted VM) — the entire savings comes from what Claude has to read: a few hundred tokens of text versus thousands of tokens of image.

**Does hybrid beat Haiku specifically?** Yes, but by a smaller margin than it beats Sonnet or Opus — only ~1.5x, not ~2.6x. That's because Haiku's direct-image cost already uses Claude's *cheaper* standard-resolution tier (1,568 visual tokens), while Sonnet/Opus direct-image is stuck paying for the high-res tier (4,784 tokens) — there's simply less input-token fat to trim off Haiku to begin with.

**A sharper question worth asking, though: does Hybrid-Sonnet beat plain Direct-Haiku?** No — and this is the one non-obvious result in this whole comparison. Direct-image Haiku (~$0.0044/page) is *cheaper* than Hybrid-Sonnet (~$0.0059/page), even though Hybrid-Sonnet only sends text. Ranked cheapest to most expensive, intro pricing: **Hybrid-Haiku ($0.0029) < Direct-Haiku ($0.0044) < Hybrid-Sonnet ($0.0059) < Hybrid-Opus ($0.0148) < Direct-Sonnet ($0.0152) < Direct-Opus ($0.0379).** So if raw cost is the only goal, Hybrid-Haiku wins outright — but remember section 4.4's core caveat still applies: any "Hybrid" option, regardless of which model does the restructuring, never lets Claude see the actual handwriting, so it can't fix an OCR misread the way Direct-Haiku (or any direct-image option) can.

**Why it isn't a free upgrade.** The whole reason to route to Claude at all (per section 5 below) is "this reads messy handwriting better than Pix2Text does." That benefit only exists if Claude actually looks at the photo. In the hybrid setup, Claude never sees the handwriting — it only sees whatever Pix2Text already transcribed, right or wrong. If Pix2Text misread a symbol, Claude can restructure and clean up formatting around that mistake, but it has no way to know the mistake happened, let alone fix it, because the source of truth (the image) isn't in front of it anymore. So the hybrid approach can make output look more polished (better structure, consistent notation, real sentences around the math) but it inherits Pix2Text's accuracy ceiling exactly — it cannot buy back the accuracy Claude's vision would have added.

**Practical implication for the tier design:** these are two genuinely different products, not two prices for the same thing:
- **Formatting/structure cleanup** (hybrid, cheap, ~$0.006-0.015/page): worth doing if the complaint is "the raw output is correct but looks rough/disorganized."
- **Accuracy improvement on messy handwriting** (direct image, full price, ~$0.015-0.038/page): the only option if the complaint is "Pix2Text is misreading my handwriting," since fixing that requires Claude to actually see it.

A three-tier product (free Pix2Text-only → cheap hybrid "Polish" tier → full-price direct-image "Premium" tier) was considered at one point, but the actual decision (section 5) went simpler: skip Claude entirely, at least for now.

---

## 5. Actual tier structure: two tiers, one engine, no Claude

**Free — 50 pages/month, Pix2Text.** Rate-limited per IP as already built (SRS NFR-022, 60 requests/hour), plus a 50-page/month account cap.

**Paid — $5/year, up to 500 pages/month, same Pix2Text engine.** Not a per-page charge, not a monthly subscription — one flat annual price. No Claude, no accuracy difference from the free tier, just 10x the monthly room. The 500-page figure is a soft target for now (enforce it the same simple way as the free cap); it isn't a hard technical limit that needs new infrastructure to exist.

**Why one flat annual price instead of metered billing:** the $1-per-1,000-pages idea from an earlier draft of this file ran into a real problem — a Stripe-style transaction fee (~2.9% + $0.30) eats roughly a third of a literal $1 charge, before any compute cost is even counted. A single $5/year charge pays that processor fee *once*, not every time someone tops up, and is a far simpler thing to build, market, and explain than metered pricing.

### 5.1 Is $5/year actually sustainable?

Using the serverless GPU rates from section 3.1 (Modal ~$0.0007/page, RunPod ~$0.00011/sec ≈ $0.0004/page) — Pix2Text is the only model being served, so these are the real relevant numbers now, not the Claude figures in section 4.

$5/year is ~$0.4167/month in revenue. At the 500-page/month soft cap:

| Platform | Cost at 500 pages/month | Monthly margin | Annual margin |
|---|---|---|---|
| Modal (~$0.0007/page) | $0.35 | $0.067 | ~$0.80 |
| RunPod (~$0.0004/page) | $0.20 | $0.217 | ~$2.60 |

Thin on Modal, healthy on RunPod — but positive either way, *even if a paying user maxes out the cap every single month of the year.* Realistically most paying users won't hit 500 pages/month consistently (the worked example in section 7 shows a genuinely active student uses far less), so actual margins per paying user will usually be better than this worst-case table. The breakeven point — where a user's usage exactly wipes out the $5 — is ~595 pages/month on Modal or ~1,042 pages/month on RunPod; someone would have to blow well past the 500-page soft cap, consistently, for a full year, to actually lose money.

### 5.2 What this gives up, on purpose

No Claude-level accuracy option, at least for now — a user with very messy handwriting that Pix2Text struggles with has no fallback in this design. Section 4's research stays in this file specifically so that door isn't closed forever: if real users hit that accuracy ceiling often enough to matter, the cost math for adding a Claude-routed tier back in is already worked out above, not something to redo from scratch.

---

## 6. Honest caveats

- The OpenAI image-token cost estimate in section 4.2 is a rough approximation, not sourced from a confirmed formula the way Claude's is — re-verify against OpenAI's current docs before using it for real billing decisions.
- These per-page cost estimates assume one photo per page. A multi-region page with several separate photos, or a very large/high-DPI scan, could cost more.
- Actual output length (and therefore output token cost) depends heavily on how much math is actually on a page — a page with ten equations costs more in output tokens than a page with one.
- Anthropic's pricing includes a batch-processing discount (50% off) for non-real-time workloads; not used in the estimates above since users expect a live result, not a delayed batch response.
- The hybrid (section 4.4) token estimates assume ~150 tokens of raw LaTeX per page; a page with many equations produces more text and costs slightly more than shown, though still far less than the image-token cost it's being compared against.
- The $5/year sustainability table (5.1) assumes a paying user's usage is served entirely by serverless compute at the rates in section 3.1 — if usage ever grows enough to justify a dedicated VM instead (past the ~50,000-60,000 pages/month crossover in 3.1), the actual per-page cost for paid users would be even lower than shown here, not higher.
- The 500-page/month soft cap on the paid tier isn't enforced by any code yet — it's a pricing assumption to build toward, not a limit that exists today.

---

## 7. Worked example: one average student, one semester

A concrete run of the numbers above, for a single typical user: **12 pages/week of homework, over a 4-month term.**

**Assumptions, stated plainly:** 4 months modeled as a 16-week semester (192 pages total, ~48 pages/month average) — a common school-term length, but real semesters run anywhere from ~14-18 weeks, so treat 192 as a representative number, not an exact one. 48 pages/month is just barely under the Free tier's 50-page cap (section 5) — this student is close to the realistic ceiling of what "free" comfortably covers, not a token light user.

### 7.1 What it actually costs (Free tier, as built)

| | Per month | Over the 4-month term |
|---|---|---|
| What the student pays | $0 | $0 |
| Marginal infra cost to us (serverless, section 3.1) | ~48 pages × $0.0004-0.0007 ≈ $0.02-$0.03 | ~$0.08-$0.13 |

This is the entire point of the free tier: a genuinely active student (not a token light user — 48 of the 50-page cap) costs us pennies for the whole term, on serverless, and costs them nothing.

### 7.2 What the student would have paid without a free tier

If there were no free option at all, this student would need the $5/year paid tier (section 5) just to use the product — their usage fits trivially inside its 500-page/month cap.

**Money the free tier saves this student: the full $5/year fee**, since 48 pages/month would otherwise force them onto the paid plan even though they're using less than a tenth of what it allows. That's a small number in absolute terms — the whole point of a $5/year price is that it's small — but the free tier is what makes the product genuinely free for the large majority of students who never need more than 50 pages/month.

### 7.3 What using Claude instead would have cost (hypothetical — not the actual design)

Section 4's Claude research isn't part of the shipped product, but it's worth keeping the comparison concrete: if this student's 192 pages had instead been sent to Claude every time (the road not taken), the raw API cost would have been:

| Model | Raw API cost, over 4 months |
|---|---|
| Hybrid: Pix2Text text → Claude Haiku restructure (section 4.4) | **~$0.56** |
| Claude Haiku 4.5 (direct image) | **~$0.84** |
| Hybrid: Pix2Text text → Claude Sonnet 5 restructure (intro pricing) | **~$1.13** |
| Claude Sonnet 5 (direct image, intro pricing) | **~$2.92** |
| Claude Opus 4.8 (direct image) | **~$7.28** |

That's the real reason a $5/year, Pix2Text-only product is viable at all: even the *cheapest* Claude option (~$0.56/semester) already costs more than a whole year of this student's Free-tier usage on serverless (~$0.08-$0.13/semester). Routing to Claude for a typical user would have cost more than what we're charging paying users for a full year.

### 7.4 The honest takeaway

For a typical active student, Free costs us pennies and costs them nothing; a heavier user pays $5/year for 10x the room, and the economics hold even in the worst case (section 5.1). The trade this design makes on purpose: no Claude-level accuracy option for the minority of users whose handwriting Pix2Text genuinely struggles with. That's a real gap, not a hidden one — and section 4's numbers are sitting right there if it's ever worth closing.

---

## Sources

- [Claude Platform pricing](https://platform.claude.com/docs/en/about-claude/pricing) — Anthropic, accessed July 2026
- [Claude vision docs — image resolution and token cost](https://platform.claude.com/docs/en/build-with-claude/vision) — Anthropic, accessed July 2026
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) — OpenAI, accessed July 2026
- [Modal pricing](https://modal.com/pricing) — Modal, accessed July 2026
- [RunPod serverless pricing](https://docs.runpod.io/serverless/pricing) — RunPod, accessed July 2026
- [Cloud Run GPU support](https://docs.cloud.google.com/run/docs/configuring/services/gpu) — Google Cloud, accessed July 2026
