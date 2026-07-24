// These shapes mirror exactly what api/routers/ocr.py's SSE messages
// contain -- {"page": n, "total": n, "result": {...}} per successful page,
// or {"page": n, "total": n, "error": "..."} for a per-page failure, or
// {"error": "..."} with no "page" key for a whole-request failure (like an
// encrypted PDF). Keeping frontend types matched to the real backend
// response, not guessed, avoids exactly the kind of bug we hit in
// inference.py earlier.
//
// WHY this lives in its own file now (moved out of UploadFlow.tsx, M5): the
// history (lib/history.ts) and share view (app/share/[id]/page.tsx) both
// need the same shapes UploadFlow already used internally -- pulling them
// out once here avoids three slightly-different copies drifting apart.
export interface Region {
  latex: string;
  type: string;
  bbox: number[][] | null;
  confidence: number | null;
  // WHY optional, only present on "page"-type regions (inference.py's
  // _collapse_to_page_result): a page-level region's single `confidence`
  // is an AVERAGE across everything Pix2Text/PaddleOCR detected on the
  // page, which can hide one genuinely bad section behind an otherwise-fine
  // score. These two fields let the UI show that breakdown honestly --
  // "N of M original sections were below the confidence threshold" --
  // without claiming to know exactly WHICH words those were (true
  // per-word highlighting isn't reliable once Groq's full-page correction
  // may have rewritten the text; see that function's docstring).
  region_count?: number;
  low_confidence_count?: number;
}

export interface PageResult {
  page: number;
  total: number;
  result?: { regions: Region[]; confidence_mean: number | null };
  error?: string;
}
