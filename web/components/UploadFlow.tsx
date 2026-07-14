"use client";

import { useEffect, useMemo, useState } from "react";
import UploadDropzone from "./UploadDropzone";
import EditableLatexRegion from "./EditableLatexRegion";
import { downloadBlob } from "@/lib/download";
import { apiUrl } from "@/lib/apiBase";
import type { PageResult } from "@/lib/types";
import { getHistory, saveToHistory, deleteHistoryEntry, clearHistory, type HistoryEntry } from "@/lib/history";

type Status = "idle" | "uploading" | "done" | "error";

export default function UploadFlow() {
  const [files, setFiles] = useState<File[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [pages, setPages] = useState<PageResult[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Edited LaTeX, separate from the original OCR result in `pages`. Keyed
  // by "pageNumber-regionIndex" so every region has its own independent
  // edit slot. We keep this separate (instead of mutating `pages` directly)
  // so the original OCR output is never lost -- useful if we want a
  // "revert to original" feature later, and keeps "what the model said"
  // cleanly separate from "what the user corrected."
  const [editedLatex, setEditedLatex] = useState<Record<string, string>>({});
  // FR-005: lets handleCancel below stop the in-flight fetch. Kept in state
  // (not a plain local variable) so the Cancel button, rendered from this
  // same component, can reach the same controller that started the request.
  const [abortController, setAbortController] = useState<AbortController | null>(null);
  const [wasCancelled, setWasCancelled] = useState(false);
  // FR-007 (Should): optional preprocessing for faint/low-quality scans.
  // Off by default -- autocontrast can slightly change a photo that's
  // already well-lit, so this should be something the user opts into for
  // a specific bad scan, not a silent default applied to every upload.
  const [enhanceContrast, setEnhanceContrast] = useState(false);
  // M5: browser-only history (see lib/history.ts for why this is
  // localStorage, not an account system) + share links.
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyEntries, setHistoryEntries] = useState<HistoryEntry[]>([]);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [shareError, setShareError] = useState<string | null>(null);
  const [isSharing, setIsSharing] = useState(false);
  const [shareCopyLabel, setShareCopyLabel] = useState("Copy link");

  function regionKey(page: number, index: number) {
    return `${page}-${index}`;
  }

  const isSinglePdf = files.length === 1 && files[0].type === "application/pdf";

  // WHY useMemo + useEffect for thumbnails, not just computed inline:
  // URL.createObjectURL() creates a real, temporary browser-memory reference
  // to a file's bytes. If we called it on every render without cleanup,
  // we'd leak memory -- each dropped/re-selected file would pile up an
  // object URL that's never released. useEffect's cleanup function
  // (the code returned from it) runs right before the next time this
  // effect re-runs, or when the component unmounts, which is exactly
  // where revokeObjectURL belongs.
  const thumbnailUrls = useMemo(
    () => (isSinglePdf ? [] : files.map((f) => URL.createObjectURL(f))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [files]
  );
  useEffect(() => {
    return () => thumbnailUrls.forEach((url) => URL.revokeObjectURL(url));
  }, [thumbnailUrls]);

  // WHY save-to-history lives in an effect keyed only on `status`, not
  // called directly inside handleConvert: handleConvert has two separate
  // places that reach "done" (the normal streaming-complete path, and the
  // AbortError/cancel path) -- an effect that fires once per transition
  // into "done" covers both without duplicating the save call. It only
  // depends on `status` on purpose: `pages`/`editedLatex` change further as
  // the user edits regions afterward, and re-running this on every one of
  // those edits would keep creating new history entries instead of one per
  // completed conversion. This does mean a history entry captures results
  // as they stood right when the job finished, not later edits -- a
  // deliberate simplicity tradeoff, not an oversight.
  useEffect(() => {
    if (status !== "done") return;
    const successfulPages = pages.filter((p) => p.result);
    if (successfulPages.length === 0) return; // nothing worth saving
    const label =
      files.length === 1 ? files[0].name : `${files.length} files (${successfulPages.length} pages)`;
    saveToHistory({ label, pages, editedLatex });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  function refreshHistory() {
    setHistoryEntries(getHistory());
  }

  function toggleHistory() {
    if (!historyOpen) refreshHistory();
    setHistoryOpen((open) => !open);
  }

  // Restores a past conversion's results into the viewer without touching
  // the backend at all -- everything needed (regions, edited text) was
  // already saved into localStorage at completion time.
  function loadFromHistory(entry: HistoryEntry) {
    setFiles([]);
    setPages(entry.pages);
    setEditedLatex(entry.editedLatex);
    setStatus("done");
    setErrorMessage(null);
    setWasCancelled(false);
    setShareUrl(null);
    setShareError(null);
    setHistoryOpen(false);
  }

  function handleDeleteHistoryEntry(id: string) {
    deleteHistoryEntry(id);
    refreshHistory();
  }

  function handleClearHistory() {
    clearHistory();
    refreshHistory();
  }

  // WHY this sends {page, regions} (the real per-region data), not the
  // flattened export text buildExportPages() produces below: a share link
  // should open into the same kind of viewer this app already shows --
  // editable regions with confidence badges and a live preview -- not a
  // single wall of compiled document text.
  async function handleShare() {
    setShareError(null);
    setShareUrl(null);
    setIsSharing(true);
    const sharePages = pages
      .slice()
      .sort((a, b) => a.page - b.page)
      .filter((p) => p.result)
      .map((p) => ({
        page: p.page,
        regions: p.result!.regions.map((region, i) => ({
          latex: editedLatex[regionKey(p.page, i)] ?? region.latex,
          type: region.type,
          confidence: region.confidence,
        })),
      }));
    try {
      const response = await fetch(apiUrl("/api/share"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pages: sharePages }),
      });
      if (!response.ok) throw new Error("Could not create share link");
      const data = await response.json();
      setShareUrl(`${window.location.origin}/share/${data.id}`);
    } catch (err) {
      setShareError(err instanceof Error ? err.message : "Could not create share link");
    } finally {
      setIsSharing(false);
    }
  }

  async function handleCopyShareUrl() {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setShareCopyLabel("Copied");
    } catch {
      setShareCopyLabel("Failed");
    }
    setTimeout(() => setShareCopyLabel("Copy link"), 1500);
  }

  async function handleConvert() {
    if (files.length === 0) return;
    setStatus("uploading");
    setPages([]);
    setErrorMessage(null);
    setWasCancelled(false);
    const controller = new AbortController();
    setAbortController(controller);

    // Decide which backend endpoint to call, matching SDD Flow A (one PDF)
    // vs Flow B (one or more images) -- both converge on the same result
    // shape, so everything below this point doesn't need to care which
    // path was used.
    const endpoint = apiUrl(isSinglePdf ? "/api/ocr/pdf" : "/api/ocr/images");

    // FormData is the browser's built-in way of building a multipart file
    // upload -- the same format an HTML <form> uses when it has a file
    // input. fetch() sends it directly; we don't hand-construct any of the
    // multipart encoding ourselves.
    const formData = new FormData();
    if (isSinglePdf) {
      formData.append("file", files[0]);
    } else {
      files.forEach((f) => formData.append("files", f));
    }
    // FastAPI's Form(bool) parses the string "true"/"false" -- sending the
    // JS boolean directly would stringify to the same thing via FormData,
    // but spelling it out avoids relying on that implicit conversion.
    formData.append("enhance_contrast", enhanceContrast ? "true" : "false");

    try {
      const response = await fetch(endpoint, {
        method: "POST",
        body: formData,
        signal: controller.signal,
      });
      if (!response.body) throw new Error("Server did not return a stream");

      // response.body is a ReadableStream of raw bytes -- fetch doesn't
      // know or care that it's SSE text, it just hands us chunks as they
      // arrive over the network. getReader() lets us pull those chunks
      // one at a time; TextDecoder turns the raw bytes into actual text.
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        // SSE messages are separated by a blank line ("\n\n") -- see
        // routers/ocr.py's `_sse()` helper, which is what writes them in
        // this exact format. A single network chunk might contain zero,
        // one, or several complete messages, plus possibly a partial one
        // at the end -- so we split on the separator, keep whatever looks
        // incomplete (the last piece) in `buffer` for next time, and only
        // process the complete messages now.
        const messages = buffer.split("\n\n");
        buffer = messages.pop() ?? "";

        for (const message of messages) {
          if (!message.startsWith("data: ")) continue;
          const payload = JSON.parse(message.slice("data: ".length));
          if (payload.error && payload.page === undefined) {
            // Whole-request error (e.g. encrypted PDF) -- no page number.
            setErrorMessage(payload.error);
            setStatus("error");
            return;
          }
          setPages((prev) => [...prev, payload]);
        }
      }

      setStatus("done");
    } catch (err) {
      // WHY this branch exists at all: calling controller.abort() (from
      // handleCancel below) makes the in-flight fetch/reader reject with a
      // DOMException named "AbortError" -- that's not a real failure, it's
      // the user's own request to stop. Treating it as such (going to
      // "done" with whatever pages already streamed in, not "error") is
      // what makes FR-005's "keep already-completed pages" true: the
      // `pages` state was already being appended to as each SSE message
      // arrived above, so nothing needs to be salvaged here, we just need
      // to stop pretending this was a crash.
      if (err instanceof DOMException && err.name === "AbortError") {
        setWasCancelled(true);
        setStatus("done");
        return;
      }
      setErrorMessage(err instanceof Error ? err.message : "Something went wrong");
      setStatus("error");
    } finally {
      setAbortController(null);
    }
  }

  function handleCancel() {
    abortController?.abort();
  }

  function handleReset() {
    setFiles([]);
    setStatus("idle");
    setPages([]);
    setErrorMessage(null);
    setEditedLatex({});
    setWasCancelled(false);
    setExportWarnings([]);
    setExportError(null);
    setShareUrl(null);
    setShareError(null);
  }

  // WHY wrapping matters: Pix2Text's region.latex is bare math source, like
  // "x^{2}+3x=7" -- valid ONLY inside a LaTeX math environment. Dropping it
  // into a document as plain paragraph text makes LaTeX try to read `^` as
  // regular text, which it isn't allowed to do outside math mode -- that's
  // exactly the "Missing $ inserted" compile error. `\[ ... \]` marks a
  // standalone (display) equation on its own line; `$ ... $` marks inline
  // math sitting within a sentence. A "text" region isn't math at all, so
  // it should NOT be wrapped -- but it might still contain characters
  // LaTeX treats as special outside math mode (%, &, #, _, $), so those
  // get escaped instead so they print literally rather than breaking
  // compilation.
  function escapeLatexText(text: string): string {
    return text.replace(/([%&#_$])/g, "\\$1");
  }

  // WHY this substitution: `\fbox` always renders its contents in LR/text
  // mode, even when it appears inside math mode -- so math-only commands
  // like `\left`/`\right` used inside an `\fbox{...}` break compilation
  // ("Missing $ inserted"), a real failure we hit and traced to this exact
  // pattern. `\boxed{...}` (from amsmath, already in our preamble) is the
  // math-mode-safe equivalent -- same visual result, doesn't break. Pix2Text
  // seems to reach for `\fbox` specifically when the original handwriting
  // had a hand-drawn box around an answer, so this isn't a one-off fluke.
  //
  // `\textcircled{...}` has the exact same underlying problem: it also
  // forces its argument into text/LR mode, even inside math mode. Plain
  // digits/letters inside it (Pix2Text's usual case, e.g. circled step
  // numbers "①"/"②") are harmless either way. But sometimes Pix2Text puts
  // an actual math command inside it (we hit `\textcircled{\div}`), which
  // breaks the same way `\fbox` did. There's no safe math-mode equivalent
  // of \textcircled, so rather than crash the export, we degrade
  // gracefully: drop the circle and keep just the underlying symbol.
  function fixKnownBadPatterns(latex: string): string {
    return latex
      .replace(/\\fbox/g, "\\boxed")
      .replace(/\\textcircled\{([^}]*)\}/g, (match, inner) =>
        inner.includes("\\") ? inner : match
      );
  }

  // WHY this check exists: a region with an unclosed brace -- most often a
  // truncated `\frac{numerator}{denom` from a misread OCR box -- isn't just
  // wrong, it's fatal to the WHOLE export if it reaches the compiler as-is.
  // LaTeX reads braces as "keep scanning for the matching close," so an
  // unclosed `{` makes it consume every token after it, including
  // `\end{document}`, until the file physically ends -- which is exactly
  // the "File ended while scanning use of \frac" crash. Counting brace
  // depth here lets us catch that BEFORE sending anything to Tectonic,
  // instead of after a failed compile. `\{` and `\}` (escaped, meaning a
  // literal brace character rather than grouping) are skipped so they
  // don't get miscounted -- a rare case in OCR output, but cheap to handle.
  function hasBalancedBraces(latex: string): boolean {
    let depth = 0;
    for (let i = 0; i < latex.length; i++) {
      const isEscaped = latex[i - 1] === "\\";
      if (latex[i] === "{" && !isEscaped) depth++;
      else if (latex[i] === "}" && !isEscaped) depth--;
      if (depth < 0) return false; // a stray `}` with nothing open
    }
    return depth === 0;
  }

  function formatRegionForExport(
    latex: string,
    type: string
  ): { text: string; wasValid: boolean } {
    const fixed = fixKnownBadPatterns(latex);
    if (!hasBalancedBraces(fixed)) {
      // Swap the broken source for a harmless comment instead of a real
      // math command -- the rest of the page (and every other page) still
      // compiles normally, and the user sees exactly which region was
      // dropped and why via the warning banner below.
      return { text: "% [region omitted: unbalanced braces in source]", wasValid: false };
    }
    if (type === "isolated") return { text: `\\[\n${fixed}\n\\]`, wasValid: true };
    if (type === "embedding") return { text: `$${fixed}$`, wasValid: true };
    return { text: escapeLatexText(fixed), wasValid: true };
  }

  // Builds the {pages: [{latex}]} shape routers/export.py expects -- one
  // combined LaTeX blob per page, using the EDITED text if the user
  // changed it, falling back to the original OCR text otherwise. This is
  // the one place "what actually gets exported" is decided. Also returns
  // `warnings` -- human-readable notes on any region that got skipped for
  // being unrecoverably malformed, so the caller can tell the user.
  function buildExportPages(): { exportPages: { latex: string }[]; warnings: string[] } {
    const warnings: string[] = [];
    const exportPages = pages
      .slice()
      .sort((a, b) => a.page - b.page)
      .filter((p) => p.result)
      .map((p) => {
        const regionTexts = p.result!.regions.map((region, i) => {
          const latex = editedLatex[regionKey(p.page, i)] ?? region.latex;
          const formatted = formatRegionForExport(latex, region.type);
          if (!formatted.wasValid) {
            warnings.push(`Page ${p.page + 1}, region ${i + 1}: skipped (invalid LaTeX, unbalanced braces)`);
          }
          return formatted.text;
        });
        return { latex: regionTexts.join("\n\n") };
      });
    return { exportPages, warnings };
  }

  const [isExportingPdf, setIsExportingPdf] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportWarnings, setExportWarnings] = useState<string[]>([]);

  async function handleDownloadTex() {
    setExportError(null);
    const { exportPages, warnings } = buildExportPages();
    setExportWarnings(warnings);
    try {
      const response = await fetch(apiUrl("/api/export/tex"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pages: exportPages }),
      });
      if (!response.ok) throw new Error(await response.text());
      downloadBlob("mathscan-export.tex", await response.blob());
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Could not export .tex file");
    }
  }

  async function handleDownloadPdf() {
    setExportError(null);
    setIsExportingPdf(true);
    const { exportPages, warnings } = buildExportPages();
    setExportWarnings(warnings);
    try {
      const response = await fetch(apiUrl("/api/export/pdf"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pages: exportPages }),
      });
      if (!response.ok) {
        // FastAPI's HTTPException body is JSON like {"detail": "..."} --
        // parse it to show the real compilation error, not just "failed."
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail ?? "PDF export failed");
      }
      downloadBlob("mathscan-export.pdf", await response.blob());
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Could not export PDF");
    } finally {
      setIsExportingPdf(false);
    }
  }

  const isUploading = status === "uploading";
  const sortedPages = pages.slice().sort((a, b) => a.page - b.page);
  const total = pages[0]?.total ?? files.length;
  const latestPageNumber = sortedPages.length > 0 ? sortedPages[sortedPages.length - 1].page + 1 : 0;

  return (
    <div className="w-full max-w-4xl">
      {status === "idle" && (
        <div className="mx-auto mb-2 flex max-w-xl justify-end">
          <button
            onClick={toggleHistory}
            className="rounded-md px-2 py-1 text-xs font-medium text-neutral-500 hover:bg-neutral-100 hover:text-neutral-700"
          >
            {historyOpen ? "Hide history" : "History"}
          </button>
        </div>
      )}

      {/* M5: browser-only history -- see lib/history.ts for why this is
          localStorage rather than a backend account system. Only shown in
          the idle state, same reasoning as the History button above. */}
      {status === "idle" && historyOpen && (
        <div className="mx-auto mb-4 max-w-xl rounded-xl border border-neutral-200 p-3">
          {historyEntries.length === 0 ? (
            <p className="py-2 text-center text-sm text-neutral-400">
              No past conversions on this device yet.
            </p>
          ) : (
            <>
              <ul className="divide-y divide-neutral-100">
                {historyEntries.map((entry) => (
                  <li key={entry.id} className="flex items-center justify-between gap-2 py-2">
                    <button
                      onClick={() => loadFromHistory(entry)}
                      className="min-w-0 flex-1 truncate text-left text-sm text-neutral-700 hover:text-accent-700"
                      title={entry.label}
                    >
                      <span className="truncate">{entry.label}</span>
                      <span className="ml-2 text-xs text-neutral-400">
                        {new Date(entry.createdAt).toLocaleDateString()}
                      </span>
                    </button>
                    <button
                      onClick={() => handleDeleteHistoryEntry(entry.id)}
                      className="shrink-0 rounded-md px-2 py-1 text-xs text-neutral-400 hover:bg-red-50 hover:text-red-600"
                      title="Delete this entry"
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
              <button
                onClick={handleClearHistory}
                className="mt-2 text-xs text-neutral-400 hover:text-red-600"
              >
                Clear all history
              </button>
            </>
          )}
        </div>
      )}

      {status === "idle" || status === "uploading" ? (
        <div className="mx-auto max-w-xl">
          <UploadDropzone files={files} onFilesChange={setFiles} disabled={isUploading} />

          {files.length > 0 && (
            <label className="mt-3 flex items-center gap-2 text-sm text-neutral-600">
              <input
                type="checkbox"
                checked={enhanceContrast}
                onChange={(e) => setEnhanceContrast(e.target.checked)}
                disabled={isUploading}
                className="h-4 w-4 rounded border-neutral-300 text-accent-700 focus:ring-accent-500"
              />
              Enhance contrast (for faint pencil or low-quality scans)
            </label>
          )}

          {files.length > 0 && (
            <div className="mt-4 flex gap-2">
              <button
                onClick={handleConvert}
                disabled={isUploading}
                className="w-full rounded-xl bg-accent-700 px-6 py-3 text-sm font-medium text-white transition-colors hover:bg-accent-800 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isUploading ? "Converting..." : "Convert to LaTeX"}
              </button>
              {/* FR-005: cancel a multi-page job mid-stream. Only shown
                  while uploading -- nothing to cancel once it's done. */}
              {isUploading && (
                <button
                  onClick={handleCancel}
                  className="shrink-0 rounded-xl border border-neutral-300 px-4 py-3 text-sm font-medium text-neutral-600 hover:bg-neutral-50"
                >
                  Cancel
                </button>
              )}
            </div>
          )}

          {/* FR-004 (Must): show explicit "processing page X of N" progress,
              not just a generic spinner -- the user should know how far
              through a multi-page job they are. */}
          {isUploading && (
            <div className="mt-4">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-200">
                <div
                  className="h-full bg-accent-600 transition-all duration-300"
                  style={{ width: total > 0 ? `${(sortedPages.length / total) * 100}%` : "5%" }}
                />
              </div>
              <p className="mt-2 text-center text-xs text-neutral-500">
                {sortedPages.length === 0
                  ? "Starting..."
                  : `Processed page ${latestPageNumber} of ${total}`}
              </p>
            </div>
          )}
        </div>
      ) : null}

      {errorMessage && (
        <p className="mx-auto mt-4 max-w-xl rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {errorMessage}
        </p>
      )}

      {wasCancelled && (
        <p className="mx-auto mt-4 max-w-xl rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Cancelled after {sortedPages.length} of {total} page{total === 1 ? "" : "s"} -- the
          pages below finished before you stopped it and can still be edited or exported.
        </p>
      )}

      {sortedPages.length > 0 && (
        <div className="mt-8 flex gap-6">
          {/* Thumbnail sidebar -- SDD 3.1: "left sidebar with page thumbnails."
              For images we have the real file to preview; for a PDF we don't
              render per-page previews client-side yet (that needs pdf.js,
              deferred past M2's scope), so we show a plain numbered icon
              instead of pretending we have a real thumbnail. */}
          {sortedPages.length > 1 && (
            <div className="flex w-20 shrink-0 flex-col gap-2">
              {sortedPages.map((p) => (
                <a
                  key={p.page}
                  href={`#page-${p.page}`}
                  className="flex flex-col items-center gap-1 rounded-lg border border-neutral-200 p-1 text-center hover:border-accent-400"
                >
                  {thumbnailUrls[p.page] ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={thumbnailUrls[p.page]}
                      alt={`Page ${p.page + 1}`}
                      className="h-16 w-full rounded object-cover"
                    />
                  ) : (
                    <div className="flex h-16 w-full items-center justify-center rounded bg-neutral-100 text-neutral-400">
                      <PdfIcon className="h-6 w-6" />
                    </div>
                  )}
                  <span className="text-[10px] text-neutral-500">Page {p.page + 1}</span>
                </a>
              ))}
            </div>
          )}

          <div className="flex-1 space-y-6 text-left">
            {sortedPages.map((page) => (
              <div
                key={page.page}
                id={`page-${page.page}`}
                className="scroll-mt-6 rounded-xl border border-neutral-200 p-4 shadow-card"
              >
                <p className="mb-3 text-xs font-medium uppercase tracking-wide text-neutral-400">
                  Page {page.page + 1} of {page.total}
                </p>
                {page.error && <p className="text-sm text-red-600">{page.error}</p>}
                {page.result && page.result.regions.length === 0 && (
                  <p className="text-sm text-neutral-400">No math detected on this page.</p>
                )}
                {page.result?.regions.map((region, i) => (
                  <div key={i} className="mb-5 last:mb-0">
                    <div className="mb-1 flex items-center gap-2 text-xs text-neutral-400">
                      <ConfidenceBadge confidence={region.confidence} />
                      <span>{region.type}</span>
                    </div>
                    <EditableLatexRegion
                      initialLatex={region.latex}
                      onChange={(latex) =>
                        setEditedLatex((prev) => ({ ...prev, [regionKey(page.page, i)]: latex }))
                      }
                    />
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {status === "done" && (
        <div className="mt-6 flex flex-col items-center gap-3">
          {exportError && (
            <p className="max-w-xl rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {exportError}
            </p>
          )}
          {exportWarnings.length > 0 && (
            <div className="max-w-xl rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
              <p className="font-medium">
                {exportWarnings.length} region{exportWarnings.length === 1 ? "" : "s"} left out
                of the export -- the OCR text was too garbled to compile (usually a low-confidence
                region on a hard-to-read photo):
              </p>
              <ul className="mt-1.5 list-disc space-y-0.5 pl-4">
                {exportWarnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex flex-wrap justify-center gap-3">
            <button
              onClick={handleDownloadTex}
              className="rounded-lg bg-accent-700 px-4 py-2 text-sm font-medium text-white hover:bg-accent-800"
            >
              Download .tex
            </button>
            <button
              onClick={handleDownloadPdf}
              disabled={isExportingPdf}
              className="rounded-lg border border-accent-700 px-4 py-2 text-sm font-medium text-accent-700 hover:bg-accent-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isExportingPdf ? "Compiling PDF..." : "Download PDF"}
            </button>
            {/* M5: share link -- unlike history (this device only), a share
                link is what lets someone else actually view this result;
                see api/routers/share.py for why that needs a real backend
                store instead of localStorage. */}
            <button
              onClick={handleShare}
              disabled={isSharing}
              className="rounded-lg border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-600 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSharing ? "Creating link..." : "Share"}
            </button>
            <button
              onClick={handleReset}
              className="rounded-lg border border-neutral-300 px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-50"
            >
              Convert another file
            </button>
          </div>

          {shareError && (
            <p className="max-w-xl rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {shareError}
            </p>
          )}
          {shareUrl && (
            <div className="flex max-w-xl items-center gap-2 rounded-lg border border-neutral-200 px-3 py-2 text-sm">
              <span className="truncate text-neutral-600">{shareUrl}</span>
              <button
                onClick={handleCopyShareUrl}
                className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-accent-700 hover:bg-accent-50"
              >
                {shareCopyLabel}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ConfidenceBadge({ confidence }: { confidence: number | null }) {
  if (confidence === null) {
    return <span className="rounded-full bg-neutral-200 px-2 py-0.5">n/a</span>;
  }
  // FR-022: highlight low-confidence regions so the user knows what to
  // double check; 0.70 is the SRS's default threshold.
  const isLow = confidence < 0.7;
  return (
    <span
      className={`rounded-full px-2 py-0.5 ${
        isLow ? "bg-amber-100 text-amber-800" : "bg-accent-100 text-accent-800"
      }`}
    >
      {(confidence * 100).toFixed(0)}%
    </span>
  );
}

function PdfIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
      <path d="M6 2h9l5 5v15H6z" />
      <path d="M15 2v5h5" />
    </svg>
  );
}
