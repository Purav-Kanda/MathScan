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
}

export interface PageResult {
  page: number;
  total: number;
  result?: { regions: Region[]; confidence_mean: number | null };
  error?: string;
}
