import { useState } from "react";
import { getClauseText, type ClauseTextResult } from "../api/client";

/**
 * A clause_ref citation chip that expands, on click, to show the actual
 * ICDR passage it cites (backend/app/regulation_text.py) — the "every
 * sentence is traceable" trust guarantee applied to the regulation
 * citations themselves, not just the generated draft's fact citations.
 * Fetched lazily on first expand and cached for the life of the component;
 * a citation nobody expands costs nothing.
 */
export function ClauseChip({ clauseRef }: { clauseRef: string }) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<ClauseTextResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next || result !== null || loading) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await getClauseText(clauseRef));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load regulation text");
    } finally {
      setLoading(false);
    }
  };

  return (
    <span className="inline-block max-w-full align-top">
      <button
        type="button"
        onClick={() => void toggle()}
        aria-expanded={open}
        className="inline-flex max-w-full items-start gap-1 rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-left text-xs text-slate-700 hover:bg-slate-100"
        title={clauseRef}
      >
        <span className="whitespace-normal break-words">{clauseRef}</span>
        <span className="shrink-0 text-slate-400">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="mt-1 max-w-md rounded border border-blue-100 bg-blue-50/60 p-2 text-xs text-slate-800">
          {loading && <p className="text-slate-500">Loading regulation text…</p>}
          {error && <p className="text-red-700">{error}</p>}
          {result && result.passages.length === 0 && result.unresolved.length === 0 && (
            <p className="text-slate-500">
              This citation isn&apos;t indexed for text lookup yet — see data/regulation/ for the
              source.
            </p>
          )}
          {result?.passages.map((p) => (
            <div key={p.locator} className="mb-2 last:mb-0">
              <div className="font-semibold text-slate-900">{p.locator}</div>
              <pre className="whitespace-pre-wrap break-words font-sans text-slate-700">
                {p.text}
              </pre>
              {p.truncated && (
                <p className="mt-1 italic text-slate-500">
                  Truncated — see {p.source_file} for the complete text.
                </p>
              )}
            </div>
          ))}
          {result?.unresolved.map((fragment) => (
            <p key={fragment} className="italic text-slate-500">
              Not indexed: {fragment}
            </p>
          ))}
        </div>
      )}
    </span>
  );
}
