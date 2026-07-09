# Cost Analysis: Self-Hosted OCR vs. Frontier Vision APIs

## Project: MathScan
**Date:** July 2026
**Companion to:** 09_SRS_MathScan.md, 10_SDD_MathScan.md

---

## 1. Why this file exists

MathScan's current pipeline (M0/M1, built and verified) uses Pix2Text-MFR, a small open-source OCR model, self-hosted on our own server. A natural question: general chat products like Claude or ChatGPT can already read a photo of handwritten math and return LaTeX — often with better accuracy on messy handwriting, since they can use broader reasoning. So why not just call one of those APIs directly for every request?

The answer is cost, and it's a bigger gap than it might look at first. This file works out the actual numbers, using each provider's own published pricing, and uses that math to justify a concrete product decision: a **free tier** (current self-hosted approach, near-zero marginal cost) and an optional **paid tier** (routes the full page to a frontier model like Claude, higher accuracy, real per-page cost that has to be covered by what we charge).

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
| Claude Sonnet 5 (through Aug 31, 2026) | $2/MTok | $10/MTok | **~$0.0102** | High-res tier, introductory pricing |
| Claude Sonnet 5 (from Sept 1, 2026) | $3/MTok | $15/MTok | **~$0.0153** | Same model, standard pricing after the intro period ends |
| Claude Opus 4.8 | $5/MTok | $25/MTok | **~$0.0254** | Highest quality, highest cost |
| OpenAI GPT-5.4 (approximate) | $1.25-2.50/MTok | $7.50-15/MTok | **~$0.006-0.013** | OpenAI's exact image-to-token formula wasn't available in what I could pull directly; this range is estimated from comparable per-token rates, not a confirmed image-token count — verify before relying on it for real billing |

### 4.3 Same volumes as the self-hosted table, using Claude Sonnet 5 (recommended paid-tier model)

| Pages processed per month | Cost at ~$0.015/page |
|---|---|
| 1,000 | ~$15 |
| 10,000 | ~$150 |
| 100,000 | ~$1,500 |

Notice this does **not** get cheaper per page as volume grows — it's a real, ongoing per-request bill, unlike the self-hosted flat fee. This is exactly why this can't just be the only tier: giving this away for free at any real scale would lose money predictably and by design, not by accident.

---

## 5. Proposed tier structure

**Free tier — current build, no changes needed.** Pix2Text-MFR, self-hosted, rate-limited per IP (already speced in the SRS: NFR-022, 60 requests/hour). Marginal cost is near-zero, so this can be given away broadly; the existing $50/month cost cap (NFR-060) already assumes this.

**Paid tier — new, routes the full page image directly to Claude Sonnet 5** instead of Pix2Text. This is closer to "what Claude/ChatGPT already do" — a general vision model reading the whole page in one pass, likely higher accuracy on messy handwriting, in exchange for a real per-page cost we have to recover through pricing.

### 5.1 Pricing the paid tier

At ~$0.015/page in raw API cost (Sonnet 5, post-introductory pricing), a sustainable price needs to cover: the API cost itself, payment processor fees (Stripe-style: typically ~2.9% + $0.30 per transaction — a real cost that matters a lot on small transactions), and margin to fund the free tier's fixed VM cost and general product development.

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

---

## Sources

- [Claude Platform pricing](https://platform.claude.com/docs/en/about-claude/pricing) — Anthropic, accessed July 2026
- [Claude vision docs — image resolution and token cost](https://platform.claude.com/docs/en/build-with-claude/vision) — Anthropic, accessed July 2026
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) — OpenAI, accessed July 2026
