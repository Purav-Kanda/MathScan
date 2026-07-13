// Backend base URL. Empty string means "same origin, relative paths" --
// exactly the behavior this app had through M3, when frontend and backend
// were served together. Once the backend moves to its own Modal URL
// (separate from wherever the frontend is hosted), NEXT_PUBLIC_API_URL
// needs to be set to that URL -- see web/.env.local.example.
//
// WHY this has to be a NEXT_PUBLIC_-prefixed variable: Next.js only
// inlines environment variables into the browser bundle if their name
// starts with that exact prefix -- a plain API_URL would be undefined in
// client components like UploadFlow.tsx, since browser code never sees
// the server's real environment variables otherwise.
export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}
