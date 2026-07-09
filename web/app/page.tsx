import UploadFlow from "@/components/UploadFlow";

// This file is a Server Component (no "use client" at the top) -- it's
// static layout and text that never changes based on user interaction, so
// it can render once on the server and ship as plain HTML. Only the
// interactive dropzone piece needs to be a client component; everything
// around it doesn't, which keeps the amount of JavaScript sent to the
// browser smaller.
export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center">
      <header className="w-full max-w-5xl px-6 py-6">
        <span className="text-lg font-semibold tracking-tight text-neutral-900">
          Math<span className="text-accent-700">Scan</span>
        </span>
      </header>

      <section className="flex w-full max-w-5xl flex-1 flex-col items-center px-6 pt-12 text-center">
        <h1 className="max-w-2xl text-4xl font-semibold tracking-tight text-neutral-900 sm:text-5xl">
          Handwritten math, turned into LaTeX in seconds
        </h1>
        <p className="mt-4 max-w-xl text-lg text-neutral-500">
          Upload a photo or scanned PDF of your math work. Get back clean, editable LaTeX
          you can drop straight into your notes.
        </p>

        <div className="mt-10 flex w-full flex-col items-center">
          <UploadFlow />
        </div>
      </section>

      <footer className="w-full max-w-5xl px-6 py-8 text-center text-xs text-neutral-400">
        No account needed. Your files are processed and deleted immediately after.
      </footer>
    </main>
  );
}
