import { useEffect, useState } from "react";
import { postDiff, type DiffSegment } from "../api/client";

/**
 * Draft version diffing (backend/app/diffing.py): renders a word-level diff
 * of two text snapshots. Shared between BankerDashboard's audit trail
 * (banker edit before/after) and DraftViewer's iterative-examiner panel
 * (pre-revision vs. final section text) — both are just "two strings the
 * caller already has" from POST /api/diff's point of view.
 */
function DiffSegments({ segments }: { segments: DiffSegment[] }) {
  return (
    <>
      {segments.map((seg, i) => {
        if (seg.kind === "equal") return <span key={i}>{seg.text}</span>;
        if (seg.kind === "delete") {
          return (
            <del key={i} className="bg-red-100 text-red-800 no-underline line-through">
              {seg.text}
            </del>
          );
        }
        return (
          <ins key={i} className="bg-emerald-100 text-emerald-800 no-underline">
            {seg.text}
          </ins>
        );
      })}
    </>
  );
}

/** Falls back to plain side-by-side before/after text on a fetch error
 * rather than hiding the content the caller asked to display. */
export default function TextDiff({ before, after }: { before: string; after: string }) {
  const [segments, setSegments] = useState<DiffSegment[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSegments(null);
    setFailed(false);
    postDiff(before, after)
      .then((data) => {
        if (!cancelled) setSegments(data);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [before, after]);

  if (failed) {
    return (
      <div className="mt-2 grid gap-2 md:grid-cols-2">
        <div>
          <div className="text-xs font-medium text-slate-600">Before</div>
          <div className="whitespace-pre-wrap text-xs text-slate-700 bg-slate-50 rounded p-2">
            {before || "(empty)"}
          </div>
        </div>
        <div>
          <div className="text-xs font-medium text-slate-600">After</div>
          <div className="whitespace-pre-wrap text-xs text-slate-700 bg-slate-50 rounded p-2">
            {after || "(empty)"}
          </div>
        </div>
      </div>
    );
  }

  if (segments === null) {
    return <p className="mt-2 text-xs text-slate-500">Computing diff…</p>;
  }

  return (
    <div className="mt-2 whitespace-pre-wrap text-xs text-slate-800 bg-slate-50 rounded p-2">
      <DiffSegments segments={segments} />
    </div>
  );
}
