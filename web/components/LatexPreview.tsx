"use client";

import katex from "katex";
import { useMemo } from "react";

// KaTeX's `renderToString` takes a LaTeX source string and returns actual
// HTML (with inline styles/positioning) that displays as typeset math --
// this is the FR-020 requirement: "render the returned LaTeX using KaTeX
// in-browser." `useMemo` avoids re-running this conversion on every
// re-render if the `latex` string itself hasn't changed.
export default function LatexPreview({ latex }: { latex: string }) {
  const html = useMemo(() => {
    try {
      return katex.renderToString(latex, { throwOnError: false, displayMode: true });
    } catch {
      // Malformed LaTeX the OCR produced -- fall back to showing the raw
      // string rather than crashing the whole page over one bad region.
      return latex;
    }
  }, [latex]);

  // WHY dangerouslySetInnerHTML: KaTeX gives us a string of real HTML tags
  // (like `<span class="katex">...`), not React components. React normally
  // escapes strings you render (so `<b>` shows as literal text, not bold)
  // as an XSS safety default. This prop deliberately opts out of that for
  // this one spot, because we specifically want KaTeX's HTML to become
  // real markup. It's "dangerous" in general (never do this with raw user
  // text) but fine here because KaTeX itself sanitizes its output.
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}
