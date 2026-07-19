"use client";

import katex from "katex";
import { useMemo } from "react";

// WHY this component now needs to handle MIXED content (plain prose with
// embedded \[ ... \] / $ ... $ math), not just one pure-math string: M6.5
// (see 12_Code_Walkthrough_MathScan.md) collapsed each page into ONE
// combined region server-side instead of many separate math-only/text-only
// regions, so a single region's `latex` can now legitimately contain both
// real sentences AND one or more math expressions in the same string.
// katex.renderToString() only knows how to render ONE math expression --
// feeding it a whole paragraph with English words mixed in either throws
// or renders garbage. This splits the source into alternating text/math
// segments first, rendering each with the right tool, then joins the
// resulting HTML back together. A region with no delimiters at all (e.g.
// an older, pre-M6.5 history entry saved before this change) just becomes
// one plain-text segment -- a known, accepted minor regression for that
// specific old-data case, not something actively handled specially.
const MATH_SEGMENT_REGEX = /(\\\[[\s\S]*?\\\]|\$[^$\n]*\$)/g;

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderMixedContent(source: string): string {
  const segments = source.split(MATH_SEGMENT_REGEX).filter((s) => s.length > 0);
  return segments
    .map((segment) => {
      const isDisplayMath = segment.startsWith("\\[") && segment.endsWith("\\]");
      const isInlineMath = segment.startsWith("$") && segment.endsWith("$") && segment.length > 1;
      if (isDisplayMath || isInlineMath) {
        const inner = isDisplayMath ? segment.slice(2, -2) : segment.slice(1, -1);
        try {
          return katex.renderToString(inner, { throwOnError: false, displayMode: isDisplayMath });
        } catch {
          // Malformed LaTeX the OCR produced -- fall back to showing the
          // raw (escaped) segment rather than crashing the whole preview
          // over one bad expression.
          return escapeHtml(segment);
        }
      }
      // A plain-text segment -- this is real OCR'd content, not trusted
      // markup, so it's escaped before becoming innerHTML. Line breaks are
      // preserved as <br /> since paragraph structure is part of what
      // M6.5's page-level correction is meant to keep intact.
      return escapeHtml(segment).replace(/\n/g, "<br />");
    })
    .join("");
}

// `useMemo` avoids re-running this conversion on every re-render if the
// `latex` string itself hasn't changed -- this is the FR-020 requirement:
// "render the returned LaTeX using KaTeX in-browser."
export default function LatexPreview({ latex }: { latex: string }) {
  const html = useMemo(() => {
    try {
      return renderMixedContent(latex);
    } catch {
      return escapeHtml(latex);
    }
  }, [latex]);

  // WHY dangerouslySetInnerHTML: KaTeX gives us a string of real HTML tags
  // (like `<span class="katex">...`), not React components. React normally
  // escapes strings you render (so `<b>` shows as literal text, not bold)
  // as an XSS safety default. This prop deliberately opts out of that for
  // this one spot, because we specifically want KaTeX's HTML to become
  // real markup. It's "dangerous" in general (never do this with raw user
  // text) but the plain-text segments above are explicitly escaped first
  // (escapeHtml), and KaTeX itself sanitizes its own output, so nothing
  // unescaped ever reaches innerHTML.
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}
