import type { PageResult } from "./types";

const STORAGE_KEY = "mathscan-history";
// WHY cap the entry count: LaTeX text is small, but localStorage still has
// a real per-origin size limit (a few MB in most browsers) -- capping at 20
// keeps months of use from ever bumping into that, instead of failing
// silently or throwing once the quota is hit.
const MAX_ENTRIES = 20;

export interface HistoryEntry {
  id: string;
  createdAt: string;
  label: string;
  pages: PageResult[];
  editedLatex: Record<string, string>;
}

// WHY browser-only (localStorage), not a backend account system: M5's
// history decision was anonymous, per-device history over real
// email/password accounts -- no database, no login UI, no session handling
// for what's still meant to be a free, no-signup app. The real tradeoff:
// history only exists on the device/browser that created it, and clearing
// site data loses it. Share links (api/routers/share.py) exist specifically
// to cover the one case this can't -- letting someone ELSE see a result.
//
// WHY typeof window checks throughout: Next.js can render this on the
// server (app/page.tsx's tree includes a server-rendered pass before the
// client takes over), where `localStorage` doesn't exist at all -- these
// guards make every function a safe no-op server-side instead of crashing.
export function saveToHistory(entry: Omit<HistoryEntry, "id" | "createdAt">): void {
  if (typeof window === "undefined") return;
  const newEntry: HistoryEntry = {
    ...entry,
    id: crypto.randomUUID(),
    createdAt: new Date().toISOString(),
  };
  // Newest first, capped -- see MAX_ENTRIES above.
  const updated = [newEntry, ...getHistory()].slice(0, MAX_ENTRIES);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
}

export function getHistory(): HistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    // A corrupted or hand-edited localStorage value shouldn't crash the
    // app -- treat it the same as "no history yet."
    return [];
  }
}

export function deleteHistoryEntry(id: string): void {
  if (typeof window === "undefined") return;
  const updated = getHistory().filter((e) => e.id !== id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
}

export function clearHistory(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(STORAGE_KEY);
}
