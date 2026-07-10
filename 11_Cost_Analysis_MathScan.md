# Cost Analysis: Self-Hosted OCR vs. Frontier Vision APIs

## Project: MathScan
**Date:** July 2026
**Companion to:** 09_SRS_MathScan.md, 10_SDD_MathScan.md

---

## 1. Why this file exists

MathScan's current pipeline (M0/M1, built and verified) uses Pix2Text-MFR, a small open-source OCR model, self-hosted on our own server. A natural question: general chat products like Claude or ChatGPT can already read a photo of handwritten math and return LaTeX — often with better accuracy on messy handwriting, since they can use broader reasoning. So why not just call one of those APIs directly for every request?

The answer is cost, and it's a bigger gap than it might look at first. This file works out the actual numbers, using each provider's own published pricing, and uses that math to justify a concrete product decision: a **free tier** (current self-hosted approach, near-zero marginal cost, capped at 150 pages/month), a **Basic top-up tier** (more of the same self-hosted OCR, $1 per 1,000 pages), and a **Premium tier** (routes the full page to a frontier model like Claude, higher accuracy, real per-page cost that has to be covered by what we charge). Full breakdown, including whether the Basic tier's price actually holds up, in section 5.

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

---

## 4. Frontier vision API cost (what the paid tier would actually cost us)

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

*(Correction from an earlier draft of this file: the Sonnet 5 and Opus 4.8 rows previously showed input-token cost only, with the ~500 output tokens left out of the total by mistake. The numbers above include both input and output, consistent with the Haiku row.)*

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
| Claude Sonnet 5 (intro pricing) | **~$0.0059** | ~$0.0152 | ~2.6x |
| Claude Sonnet 5 (post-Sept 2026) | **~$0.0089** | ~$0.0228 | ~2.6x |
| Claude Opus 4.8 | **~$0.0148** | ~$0.0379 | ~2.6x |

Pix2Text's own inference cost doesn't change either way (still $0 marginal, same self-hosted VM) — the entire savings comes from what Claude has to read: a few hundred tokens of text versus thousands of tokens of image.

**Why it isn't a free upgrade.** The whole reason to route to Claude at all (per section 5 below) is "this reads messy handwriting better than Pix2Text does." That benefit only exists if Claude actually looks at the photo. In the hybrid setup, Claude never sees the handwriting — it only sees whatever Pix2Text already transcribed, right or wrong. If Pix2Text misread a symbol, Claude can restructure and clean up formatting around that mistake, but it has no way to know the mistake happened, let alone fix it, because the source of truth (the image) isn't in front of it anymore. So the hybrid approach can make output look more polished (better structure, consistent notation, real sentences around the math) but it inherits Pix2Text's accuracy ceiling exactly — it cannot buy back the accuracy Claude's vision would have added.

**Practical implication for the tier design:** these are two genuinely different products, not two prices for the same thing:
- **Formatting/structure cleanup** (hybrid, cheap, ~$0.006-0.015/page): worth doing if the complaint is "the raw output is correct but looks rough/disorganized."
- **Accuracy improvement on messy handwriting** (direct image, full price, ~$0.015-0.038/page): the only option if the complaint is "Pix2Text is misreading my handwriting," since fixing that requires Claude to actually see it.

A three-tier product (free Pix2Text-only → cheap hybrid "Polish" tier → full-price direct-image "Premium" tier) is a reasonable structure to consider if user feedback shows people want cleanup more often than re-reading — but that's a hypothesis to validate with actual users, same caveat as the pricing numbers in section 5.

---

## 5. Proposed tier structure

Three tiers, in increasing order of what they cost us to run:

**5.0 Free — Pix2Text, capped at 150 pages/month.** Self-hosted, rate-limited per IP (SRS NFR-022, 60 requests/hour) and now also capped at 150 pages/month per account. Marginal cost per page is near-zero (section 3), so this tier is cheap to give away broadly — the cap exists to bound how much free-tier volume can pile onto the one shared VM, not because any single page costs much.

**5.1 Basic — pay-as-you-go top-up on the same Pix2Text engine, $1 per 1,000 pages (~$0.001/page).** Same accuracy as the free tier (still Pix2Text, not Claude) — this tier buys *more volume*, not *better OCR*. Intended for a user who's past the 150-page free cap but doesn't need Claude-level accuracy.

**5.2 Premium — routes the full page image directly to Claude Sonnet 5** instead of Pix2Text. This is closer to "what Claude/ChatGPT already do" — a general vision model reading the whole page in one pass, likely higher accuracy on messy handwriting, in exchange for a real per-page cost we have to recover through pricing.

### 5.1.1 Is $1 per 1,000 pages actually sustainable?

Worked through honestly, this price is tight, and likely loses money below a certain volume — worth knowing before committing to it.

**Against raw compute cost:** the self-hosted table in section 3 shows real cost per page falling as volume rises, but even at 10,000 pages/month the VM still costs ~$0.004/page. Charging $0.001/page is *below* that at any volume under ~40,000 Basic-tier pages in a month (using $40/month VM cost ÷ $0.001/page):

| Monthly VM cost | Basic-tier pages needed to break even (at $0.001/page revenue) |
|---|---|
| $30 | 30,000 |
| $40 | 40,000 |
| $50 | 50,000 |

Below that many paid pages in a month, this tier is a real loss, not a thin margin — it's subsidized by whatever else is making money (the Premium tier, mainly). That's a fine short-term choice to win users, but it shouldn't be mistaken for a profitable line item until volume clears the numbers above.

**Against payment processing fees, which matter more here than the compute cost does.** A Stripe-style transaction fee (~2.9% + $0.30) charged on a literal $1 purchase is `0.029 × $1 + $0.30 = $0.329` — roughly **a third of that $1 gone to the payment processor alone**, before any compute cost is even counted. Selling this tier in raw $1 increments is the worst way to price it. The fixed $0.30 piece only becomes a small percentage of the charge once the charge itself is bigger — e.g. a $5 pack (still $1-per-1,000-pages rate, sold as 5,000 pages for $5) drops processor overhead to `0.029 × $5 + $0.30 = $0.445`, about 9% of the charge instead of 33%. **Practical fix: never sell this tier as a $1 transaction; sell it as a pack (e.g. $5 for 5,000 pages) or fold it into the same monthly-subscription mechanism as Premium (5.2.1 below), so the $0.30 fixed fee is paid once per month, not once per top-up.**

### 5.2.1 Pricing the Premium tier

At ~$0.015/page in raw API cost (Sonnet 5, post-introductory pricing), a sustainable price needs to cover: the API cost itself, payment processor fees, and margin to fund the free/basic tiers' fixed VM cost and general product development.

Two starting options to validate with actual users, not final numbers:

- **Pay-per-page:** charge $0.05-0.08 per page — roughly 3-5x the raw API cost, which is a reasonable starting markup for a consumer product with per-transaction payment overhead.
- **Monthly subscription with a page quota** (usually converts better than metered pricing for a student audience): e.g. "$5/month for 150 pages" (~$0.033/page) or "$10/month for 400 pages" (~$0.025/page). A subscription also smooths out payment processor fees across many pages instead of eating a $0.30 minimum fee on every single tiny transaction.

This is a starting hypothesis, not a final price — real pricing should get validated against what students will actually pay, which nothing in this file can tell us on its own.

---

## 6. Honest caveats

- The OpenAI image-token cost estimate in section 4.2 is a rough approximation, not sourced from a confirmed formula the way Claude's is — re-verify against OpenAI's current docs before using it for real billing decisions.
- These per-page cost estimates assume one photo per page. A multi-region page with several separate photos, or a very large/high-DPI scan, could cost more.
- Actual output length (and therefore output token cost) depends heavily on how much math is actually on a page — a page with ten equations costs more in output tokens than a page with one.
- Anthropic's pricing includes a batch-processing discount (50% off) for non-real-time workloads; not used in the estimates above since users expect a live result, not a delayed batch response.
- The hybrid (section 4.4) token estimates assume ~150 tokens of raw LaTeX per page; a page with many equations produces more text and costs slightly more than shown, though still far less than the image-token cost it's being compared against.
- The Basic tier's break-even table (5.1.1) uses the VM's full monthly cost as the bar to clear, but that VM is also serving free-tier traffic for $0 revenue — so the real break-even volume is higher than shown, not lower, once free-tier load is accounted for.

---

## Sources

- [Claude Platform pricing](https://platform.claude.com/docs/en/about-claude/pricing) — Anthropic, accessed July 2026
- [Claude vision docs — image resolution and token cost](https://platform.claude.com/docs/en/build-with-claude/vision) — Anthropic, accessed July 2026
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) — OpenAI, accessed July 2026
