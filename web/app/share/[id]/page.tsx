"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import EditableLatexRegion from "@/components/EditableLatexRegion";
import { apiUrl } from "@/lib/apiBase";
import type { Region } from "@/lib/types";

interface SharePageData {
  page: number;
  regions: Region[];
}

// WHY a client component fetching in useEffect, not a server component
// doing the fetch at render time: this matches the same manual-fetch style
// UploadFlow.tsx already uses for talking to the backend, and keeps the
// backend URL logic in one place (lib/apiBase.ts) regardless of whether
// the request happens server- or client-side. A share link also needs a
// loading/error state (link expired, backend cold-starting) that's easiest
// to express as component state here.
export default function SharePageView() {
  const params = useParams<{ id: string }>();
  const [pages, setPages] = useState<SharePageData[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(apiUrl(`/api/share/${params.id}`))
      .then((res) => {
        if (!res.ok) throw new Error("This share link doesn't exist or has expired.");
        return res.json();
      })
      .then((data) => setPages(data.pages))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load this link."));
  }, [params.id]);

  return (
    <main className="flex min-h-screen flex-col items-center">
      <header className="w-full max-w-5xl px-6 py-6">
        <a href="/" className="text-lg font-semibold tracking-tight text-neutral-900">
          Math<span className="text-accent-700">Scan</span>
        </a>
      </header>

      <section className="w-full max-w-4xl flex-1 px-6 pb-12">
        <p className="mb-6 text-center text-sm text-neutral-500">
          A shared conversion result -- read-only for you, editing here doesn&apos;t change the
          original.
        </p>

        {error && (
          <p className="mx-auto max-w-xl rounded-lg bg-red-50 px-4 py-3 text-center text-sm text-red-700">
            {error}
          </p>
        )}

        {!error && pages === null && (
          <p className="text-center text-sm text-neutral-400">Loading...</p>
        )}

        {pages && (
          <div className="space-y-6 text-left">
            {pages
              .slice()
              .sort((a, b) => a.page - b.page)
              .map((page) => (
                <div key={page.page} className="rounded-xl border border-neutral-200 p-4 shadow-card">
                  <p className="mb-3 text-xs font-medium uppercase tracking-wide text-neutral-400">
                    Page {page.page + 1}
                  </p>
                  {page.regions.length === 0 && (
                    <p className="text-sm text-neutral-400">No math detected on this page.</p>
                  )}
                  {page.regions.map((region, i) => (
                    <div key={i} className="mb-5 last:mb-0">
                      <div className="mb-1 flex items-center gap-2 text-xs text-neutral-400">
                        <ConfidenceBadge confidence={region.confidence} />
                        <span>{region.type}</span>
                      </div>
                      {/* onChange is a no-op here on purpose -- this view
                          never sends edits back to the backend, it's just
                          reusing the same editor/preview component so a
                          visitor can copy LaTeX out or try tweaks locally. */}
                      <EditableLatexRegion initialLatex={region.latex} onChange={() => {}} />
                    </div>
                  ))}
                </div>
              ))}
          </div>
        )}
      </section>
    </main>
  );
}

// Duplicated from UploadFlow.tsx rather than shared, deliberately -- it's
// four lines and pulling it into a shared component isn't worth the extra
// indirection for something this small.
function ConfidenceBadge({ confidence }: { confidence: number | null }) {
  if (confidence === null) {
    return <span className="rounded-full bg-neutral-200 px-2 py-0.5">n/a</span>;
  }
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
