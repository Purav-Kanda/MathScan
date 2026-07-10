"use client";

import { useEffect, useState } from "react";
import LatexPreview from "./LatexPreview";

interface EditableLatexRegionProps {
  initialLatex: string;
  onChange: (latex: string) => void;
}

// FR-021: "the user shall be able to edit the LaTeX source in a text area;
// the preview shall update live (debounced 250ms)."
export default function EditableLatexRegion({ initialLatex, onChange }: EditableLatexRegionProps) {
  const [source, setSource] = useState(initialLatex);
  const [debouncedSource, setDebouncedSource] = useState(initialLatex);
  // FR-032: "copy to clipboard" feedback -- shows "Copied" for a moment
  // then reverts, so the user gets confirmation without a persistent banner.
  const [copyLabel, setCopyLabel] = useState("Copy");

  // WHY compare against initialLatex (a prop, not state): this is the raw
  // OCR output for this region, unchanged since it arrived from the
  // backend. "Has the user touched this box" is just "does source still
  // equal what we started with" -- no extra state needed to track it.
  const isEdited = source !== initialLatex;

  async function handleCopy() {
    // WHY navigator.clipboard.writeText over document.execCommand: the
    // Clipboard API is the modern standard and works without a hidden
    // textarea + selection hack; it does require a secure context (https
    // or localhost), which the app already runs under.
    try {
      await navigator.clipboard.writeText(source);
      setCopyLabel("Copied");
    } catch {
      setCopyLabel("Failed");
    }
    setTimeout(() => setCopyLabel("Copy"), 1500);
  }

  // FR-032's sibling feature: undo a manual edit back to the original OCR
  // output for this one region, without affecting any other region.
  function handleRevert() {
    setSource(initialLatex);
  }

  // WHY debounce, not just render `source` directly: without this, KaTeX
  // would re-parse and re-render on every single keystroke -- fine for one
  // region, wasteful and potentially laggy if a page has a dozen of them.
  // Debouncing means "wait until the user pauses typing for 250ms, then
  // update the preview" instead of updating on every keypress. The
  // setTimeout is cancelled and restarted on every keystroke (that's what
  // the cleanup function returned from useEffect does), so only the last
  // pause actually triggers an update.
  useEffect(() => {
    const timeout = setTimeout(() => setDebouncedSource(source), 250);
    return () => clearTimeout(timeout);
  }, [source]);

  // Report every change up to the parent immediately (not debounced) --
  // the parent needs the latest text for export regardless of whether the
  // preview has caught up yet.
  useEffect(() => {
    onChange(source);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-end gap-2">
        {isEdited && (
          <button
            type="button"
            onClick={handleRevert}
            className="rounded-md px-2 py-1 text-xs font-medium text-neutral-500 hover:bg-neutral-100 hover:text-neutral-700"
            title="Revert to the original OCR output"
          >
            Revert
          </button>
        )}
        <button
          type="button"
          onClick={handleCopy}
          className="rounded-md px-2 py-1 text-xs font-medium text-accent-700 hover:bg-accent-50"
          title="Copy LaTeX to clipboard"
        >
          {copyLabel}
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <textarea
          value={source}
          onChange={(e) => setSource(e.target.value)}
          rows={3}
          spellCheck={false}
          className="w-full resize-none rounded-lg border border-neutral-200 bg-white p-3 font-mono text-sm text-neutral-700 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
        />
        <div className="overflow-x-auto rounded-lg bg-neutral-50 p-3">
          <LatexPreview latex={debouncedSource} />
        </div>
      </div>
    </div>
  );
}
