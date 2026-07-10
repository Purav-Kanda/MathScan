// DEPRECATED / unused: superseded by lib/download.ts + the backend's
// routers/export.py, which is now the single source of truth for building
// .tex output (avoids maintaining the same LaTeX-building logic twice, in
// both Python and TypeScript). Same OneDrive-mount permission issue as
// before means this file couldn't be shortened cleanly earlier -- safe to
// delete manually in Explorer whenever convenient; nothing imports it.
export {};
