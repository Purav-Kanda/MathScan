// Generic "trigger a browser download" helper -- deliberately not specific
// to LaTeX or any one file type. The actual .tex/PDF content comes from the
// backend (routers/export.py) so there's exactly one place that knows how
// to build a MathScan export, instead of duplicating that logic in both
// Python and TypeScript.
export function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link); // Firefox requires the link to be in the DOM before .click() works
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url); // release the temporary in-memory reference now that the download's started
}
