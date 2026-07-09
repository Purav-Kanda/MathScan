"use client";

import { useEffect, useMemo, useState } from "react";
import UploadDropzone from "./UploadDropzone";
import LatexPreview from "./LatexPreview";

// These shapes mirror exactly what api/routers/ocr.py's SSE messages
// contain -- {"page": n, "total": n, "result": {...}} per successful page,
// or {"page": n, "total": n, "error": "..."} for a per-page failure, or
// {"error": "..."} with no "page" key for a whole-request failure (like an
// encrypted PDF). Keeping frontend types matched to the real backend
// response, not guessed, avoids exactly the kind of bug we hit in
// inference.py earlier.
interface Region {
  latex: string;
  type: string;
  bbox: number[][] | null;
  confidence: number | null;
}

interface PageResult {
  page: number;
  total: number;
  result?: { regions: Region[]; confidence_mean: number | null };
  error?: string;
}

type Status = "idle" | "uploading" | "done" | "error";

export default function UploadFlow() {
  const [files, setFiles] = useState<File[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [pages, setPages] = useState<PageResult[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

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

  async function handleConvert() {
    if (files.length === 0) return;
    setStatus("uploading");
    setPages([]);
    setErrorMessage(null);

    // Decide which backend endpoint to call, matching SDD Flow A (one PDF)
    // vs Flow B (one or more images) -- both converge on the same result
    // shape, so everything below this point doesn't need to care which
    // path was used.
    const endpoint = isSinglePdf ? "/api/ocr/pdf" : "/api/ocr/images";

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

    try {
      const response = await fetch(endpoint, { method: "POST", body: formData });
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
      setErrorMessage(err instanceof Error ? err.message : "Something went wrong");
      setStatus("error");
    }
  }

  function handleReset() {
    setFiles([]);
    setStatus("idle");
    setPages([]);
    setErrorMessage(null);
  }

  const isUploading = status === "uploading";
  const sortedPages = pages.slice().sort((a, b) => a.page - b.page);
  const total = pages[0]?.total ?? files.length;
  const latestPageNumber = sortedPages.length > 0 ? sortedPages[sortedPages.length - 1].page + 1 : 0;

  return (
    <div className="w-full max-w-4xl">
      {status === "idle" || status === "uploading" ? (
        <div className="mx-auto max-w-xl">
          <UploadDropzone files={files} onFilesChange={setFiles} disabled={isUploading} />

          {files.length > 0 && (
            <button
              onClick={handleConvert}
              disabled={isUploading}
              className="mt-4 w-full rounded-xl bg-accent-700 px-6 py-3 text-sm font-medium text-white transition-colors hover:bg-accent-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isUploading ? "Converting..." : "Convert to LaTeX"}
            </button>
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
                  <div key={i} className="mb-4 last:mb-0">
                    <div className="overflow-x-auto rounded-lg bg-neutral-50 p-3">
                      <LatexPreview latex={region.latex} />
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-xs text-neutral-400">
                      <ConfidenceBadge confidence={region.confidence} />
                      <code className="truncate">{region.latex}</code>
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {status === "done" && (
        <div className="mt-6 flex justify-center">
          <button
            onClick={handleReset}
            className="rounded-lg border border-neutral-300 px-4 py-2 text-sm text-neutral-600 hover:bg-neutral-50"
          >
            Convert another file
          </button>
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
