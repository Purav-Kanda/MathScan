// Client-side file validation -- SDD 3.1: "Files validated client-side
// (size + MIME) before upload." WHY bother validating here when the
// backend already rejects bad files (MAX_PDF_MB in routers/ocr.py): without
// this, a user picks a 40MB PDF, waits for it to actually upload over the
// network, and ONLY THEN finds out it was rejected. Checking here means
// they find out instantly, before wasting any upload time -- the backend
// check still exists too, as the real enforcement (never trust a check
// that only happens in the browser, since it can always be bypassed).
export const MAX_FILE_MB = 25;
export const MAX_FILE_COUNT = 50;
const ALLOWED_TYPES = ["application/pdf", "image/png", "image/jpeg", "image/webp"];

export interface ValidationResult {
  validFiles: File[];
  errors: string[];
}

export function validateFiles(files: File[]): ValidationResult {
  const errors: string[] = [];
  let candidates = files;

  if (candidates.length > MAX_FILE_COUNT) {
    errors.push(`Only ${MAX_FILE_COUNT} files can be processed at once -- extra files were dropped.`);
    candidates = candidates.slice(0, MAX_FILE_COUNT);
  }

  const validFiles = candidates.filter((file) => {
    if (!ALLOWED_TYPES.includes(file.type)) {
      errors.push(`"${file.name}" isn't a supported file type (PDF, PNG, JPG, WebP only).`);
      return false;
    }
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      errors.push(`"${file.name}" is over ${MAX_FILE_MB}MB.`);
      return false;
    }
    return true;
  });

  return { validFiles, errors };
}
