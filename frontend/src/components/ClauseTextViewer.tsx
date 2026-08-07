import { useState } from "react";
import { getClauseText } from "../api/client";

type LoadState = "idle" | "loading" | "ready" | "error";

/**
 * A clause citation chip that expands to show the actual ICDR passage.
 *
 * Every requirement in the checklist already carried a `clause_ref` label,
 * but a label is only a promise that the citation is real — the promoter had
 * no way to check it. This resolves the citation against the regulation text
 * pinned in `data/regulation/`, so "why are you asking me this?" is
 * answerable by reading the regulation rather than trusting the tool.
 *
 * Collapsed by default and fetched lazily on expand: a gap report can list
 * dozens of requirements, and eagerly pulling a passage for each would be a
 * request per row for text nobody has asked to read yet.
 *
 * When the ref can't be resolved the panel says so plainly. It never falls
 * back to a near-miss passage — showing the *wrong* regulation next to a
 * citation would undermine the exact guarantee this feature exists to
 * strengthen (see backend/app/schema/clause_text.py).
 */
export default function ClauseTextViewer({ clauseRef }: { clauseRef: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<LoadState>("idle");
  const [text, setText] = useState<string | null>(null);

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (state !== "idle") return; // already fetched — don't refetch on re-expand
    setState("loading");
    try {
      const result = await getClauseText(clauseRef);
      setText(result.found ? result.text : null);
      setState("ready");
    } catch {
      setState("error");
    }
  }

  return (
    <span className="inline-block max-w-full">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        title={clauseRef}
        className="max-w-full break-words whitespace-normal rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-left text-xs text-slate-700 hover:border-blue-300 hover:bg-blue-50 focus:ring-2"
      >
        {clauseRef}
        <span aria-hidden="true" className="ml-1 text-slate-400">
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open ? (
        <div className="mt-1.5 rounded border border-slate-200 bg-white p-2.5">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
            Regulation text
          </div>
          {state === "loading" ? (
            <p className="mt-1 text-xs text-gray-500">Loading…</p>
          ) : state === "error" ? (
            <p className="mt-1 text-xs text-red-700" role="alert">
              Could not load the regulation text.
            </p>
          ) : text ? (
            <>
              <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words font-sans text-xs leading-relaxed text-gray-800">
                {text}
              </pre>
              <p className="mt-1.5 text-[11px] text-gray-500">
                Extract from the pinned regulation text in{" "}
                <span className="font-mono">data/regulation/</span>. Read alongside the full
                regulation, not in place of it.
              </p>
            </>
          ) : (
            <p className="mt-1 text-xs text-gray-600">
              No passage indexed for this citation. The citation itself is still the
              authority — this viewer only resolves the paragraph-level references it can
              match exactly, and never shows a near-miss.
            </p>
          )}
        </div>
      ) : null}
    </span>
  );
}
