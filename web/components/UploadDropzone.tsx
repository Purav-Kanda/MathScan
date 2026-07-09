"use client";
// WHY "use client": by default, every component in Next.js's App Router
// renders on the server (faster initial load, smaller JS sent to the
// browser). But this component needs to react to drag/drop events and
// keep track of "is the user hovering a file over me right now" -- that
// requires actual browser interactivity and React state, which only works
// in a "client component." This directive is what opts this one file into
// that, while everything else in the app stays server-rendered by default.

import { useState, useRef, DragEvent, ChangeEvent } from "react";

interface UploadDropzoneProps {
  files: File[];
  onFilesChange: (files: File[]) => void;
  disabled?: boolean;
}

// WHY "controlled" (files/onFilesChange passed in as props, instead of this
// component keeping its own private list): the parent (UploadFlow) needs to
// know which files were picked, so it can actually send them to the backend
// when the user clicks "Convert." If this component kept the file list to
// itself, there'd be no way for the parent to ever get at it. This is the
// standard React pattern for "a form input whose value the parent needs."
export default function UploadDropzone({ files, onFilesChange, disabled }: UploadDropzoneProps) {
  const [isDragActive, setIsDragActive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault(); // browsers open dropped files by default -- this stops that
    if (!disabled) setIsDragActive(true);
  }

  function handleDragLeave() {
    setIsDragActive(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragActive(false);
    if (disabled) return;
    onFilesChange(Array.from(e.dataTransfer.files));
  }

  function handleFileInputChange(e: ChangeEvent<HTMLInputElement>) {
    if (e.target.files) {
      onFilesChange(Array.from(e.target.files));
    }
  }

  return (
    <div className="w-full max-w-xl">
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => !disabled && inputRef.current?.click()}
        className={`flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-8 py-16 text-center transition-colors ${
          disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"
        } ${
          isDragActive
            ? "border-accent-600 bg-accent-50"
            : "border-neutral-300 bg-neutral-50 hover:border-accent-400 hover:bg-accent-50/40"
        }`}
      >
        <UploadIcon className="mb-4 h-10 w-10 text-accent-700" />
        <p className="text-base font-medium text-neutral-900">
          Drag and drop a PDF or images here
        </p>
        <p className="mt-1 text-sm text-neutral-500">or click to browse your files</p>
        <p className="mt-4 text-xs text-neutral-400">
          PDF, PNG, JPG, or WebP &middot; up to 25MB &middot; processed and deleted after
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,image/png,image/jpeg,image/webp"
          multiple
          disabled={disabled}
          className="hidden"
          onChange={handleFileInputChange}
        />
      </div>

      {files.length > 0 && (
        <ul className="mt-4 space-y-1">
          {files.map((file) => (
            <li
              key={file.name}
              className="flex items-center justify-between rounded-lg bg-neutral-50 px-3 py-2 text-sm text-neutral-700 shadow-card"
            >
              <span className="truncate">{file.name}</span>
              <span className="ml-2 shrink-0 text-neutral-400">
                {(file.size / 1024).toFixed(0)} KB
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UploadIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 16V4M12 4l-4 4M12 4l4 4" />
      <path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
    </svg>
  );
}
