import { useCallback, useEffect, useMemo, useState } from "react";
import {
  advanceSection,
  exportPackage,
  getReviewState,
  getSchema,
  getSections,
  recordEdit,
  type BankerEdit,
  type Checklist,
  type ChecklistEntry,
  type ExportResponse,
  type GeneratedSection,
  type ReviewState,
  type SectionState,
  type Severity,
} from "../api/client";

// --------------------------------------------------------------------------
// Small helpers
// --------------------------------------------------------------------------

// The api/client fetch helpers surface HTTP errors as ``Error("POST /path → N")``.
// We pull the status back out here so we can react to 409 (workflow conflict /
// certification lock) distinctly from a generic failure.
function statusFromError(err: unknown): number | null {
  if (!(err instanceof Error)) return null;
  const match = err.message.match(/→\s*(\d{3})\b/);
  return match ? Number(match[1]) : null;
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

// Next legal step in the draft → reviewed → certified pipeline. Returning
// ``null`` means the section is already certified — nothing further to do.
function nextState(current: SectionState): SectionState | null {
  if (current === "draft") return "reviewed";
  if (current === "reviewed") return "certified";
  return null;
}

function nextStateLabel(current: SectionState): string {
  const nxt = nextState(current);
  if (nxt === "reviewed") return "Mark reviewed";
  if (nxt === "certified") return "Certify";
  return "Certified";
}

const SEVERITY_BADGE: Record<Severity, string> = {
  blocker: "bg-red-100 text-red-800 border border-red-300",
  material: "bg-amber-100 text-amber-800 border border-amber-300",
  minor: "bg-slate-100 text-slate-700 border border-slate-300",
};

const STATE_BADGE: Record<SectionState, string> = {
  draft: "bg-slate-100 text-slate-700 border border-slate-300",
  reviewed: "bg-blue-100 text-blue-800 border border-blue-300",
  certified: "bg-emerald-100 text-emerald-800 border border-emerald-300",
};

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------

interface EditFormState {
  entry_id: string;
  editor: string;
  before: string;
  after: string;
}

const EMPTY_EDIT: EditFormState = {
  entry_id: "",
  editor: "",
  before: "",
  after: "",
};

export default function BankerDashboard() {
  const [checklist, setChecklist] = useState<Checklist | null>(null);
  const [reviewState, setReviewState] = useState<ReviewState | null>(null);
  const [sections, setSections] = useState<GeneratedSection[] | null>(null);

  const [loading, setLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Per-row action state so a stuck request only blocks its own row.
  const [rowBusy, setRowBusy] = useState<Record<string, boolean>>({});
  const [rowError, setRowError] = useState<Record<string, string>>({});

  // Edit form + submission state.
  const [editForm, setEditForm] = useState<EditFormState>(EMPTY_EDIT);
  const [editBusy, setEditBusy] = useState<boolean>(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [editNotice, setEditNotice] = useState<string | null>(null);

  // Export state.
  const [exporting, setExporting] = useState<boolean>(false);
  const [exportResult, setExportResult] = useState<ExportResponse | null>(null);
  const [exportLockMessage, setExportLockMessage] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  // ----------------------------------------------------------------------
  // Initial load: schema + review state + generated sections in parallel.
  // ----------------------------------------------------------------------
  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [c, r, s] = await Promise.all([
        getSchema(),
        getReviewState(),
        getSections(),
      ]);
      setChecklist(c);
      setReviewState(r);
      setSections(s);
    } catch (err) {
      setLoadError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // ----------------------------------------------------------------------
  // Derived: only entries the tool is actually responsible for (non-stub).
  // Blocker entries first, then material, then minor — matches how the
  // banker would triage the certification queue.
  // ----------------------------------------------------------------------
  const activeEntries = useMemo<ChecklistEntry[]>(() => {
    if (!checklist) return [];
    const severityOrder: Record<Severity, number> = {
      blocker: 0,
      material: 1,
      minor: 2,
    };
    return checklist.entries
      .filter((e) => !e.stub)
      .slice()
      .sort((a, b) => {
        const s = severityOrder[a.severity] - severityOrder[b.severity];
        if (s !== 0) return s;
        return a.section.localeCompare(b.section) || a.title.localeCompare(b.title);
      });
  }, [checklist]);

  // Blocker entries that are not yet certified — this is what the lock
  // guards against. Listed for the operator so the copy is a guarantee,
  // not a mystery.
  const uncertifiedBlockers = useMemo<ChecklistEntry[]>(() => {
    if (!reviewState) return [];
    return activeEntries.filter(
      (e) =>
        e.severity === "blocker" &&
        (reviewState.states[e.id] ?? "draft") !== "certified",
    );
  }, [activeEntries, reviewState]);

  const sectionsWithText = useMemo<Set<string>>(() => {
    if (!sections) return new Set();
    return new Set(sections.map((s) => s.entry_id));
  }, [sections]);

  // ----------------------------------------------------------------------
  // Advance one section along draft → reviewed → certified.
  // ----------------------------------------------------------------------
  const onAdvance = useCallback(
    async (entry: ChecklistEntry, current: SectionState) => {
      const target = nextState(current);
      if (!target) return;

      setRowBusy((prev) => ({ ...prev, [entry.id]: true }));
      setRowError((prev) => {
        const { [entry.id]: _drop, ...rest } = prev;
        return rest;
      });
      try {
        const next = await advanceSection(entry.id, target);
        setReviewState(next);
      } catch (err) {
        const status = statusFromError(err);
        const msg =
          status === 409
            ? "Cannot advance this section right now — the workflow rejected the transition."
            : errorMessage(err);
        setRowError((prev) => ({ ...prev, [entry.id]: msg }));
      } finally {
        setRowBusy((prev) => {
          const { [entry.id]: _drop, ...rest } = prev;
          return rest;
        });
      }
    },
    [],
  );

  // ----------------------------------------------------------------------
  // Record a banker edit. The backend drops the section back to ``draft``
  // (edits invalidate certification); we surface that as a UI note so it
  // is never a surprise.
  // ----------------------------------------------------------------------
  const onSubmitEdit = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!editForm.entry_id || !editForm.editor || !editForm.after) {
        setEditError("Entry, editor and the replacement text are required.");
        return;
      }
      setEditBusy(true);
      setEditError(null);
      setEditNotice(null);
      try {
        const edit: BankerEdit = {
          entry_id: editForm.entry_id,
          editor: editForm.editor,
          before: editForm.before,
          after: editForm.after,
          // Backend fills the timestamp — send a placeholder that will be
          // overwritten server-side; the response carries the canonical value.
          at: new Date().toISOString(),
        };
        const next = await recordEdit(edit);
        setReviewState(next);
        setEditNotice(
          "Edit recorded. The section has dropped back to draft — re-review " +
            "and re-certify before exporting.",
        );
        setEditForm((prev) => ({ ...EMPTY_EDIT, editor: prev.editor }));
      } catch (err) {
        setEditError(errorMessage(err));
      } finally {
        setEditBusy(false);
      }
    },
    [editForm],
  );

  // ----------------------------------------------------------------------
  // Export. A 409 here is the certification lock — a feature, not a bug.
  // The copy is a guarantee: nothing ships until every blocker is certified.
  // ----------------------------------------------------------------------
  const onExport = useCallback(async () => {
    setExporting(true);
    setExportError(null);
    setExportLockMessage(null);
    setExportResult(null);
    try {
      const result = await exportPackage();
      setExportResult(result);
    } catch (err) {
      const status = statusFromError(err);
      if (status === 409) {
        const count = uncertifiedBlockers.length;
        setExportLockMessage(
          `Certification lock: ${count} blocker ` +
            `section${count === 1 ? "" : "s"} uncertified. ` +
            "The exchange-ready package unlocks the moment every blocker is certified.",
        );
      } else {
        setExportError(errorMessage(err));
      }
    } finally {
      setExporting(false);
    }
  }, [uncertifiedBlockers.length]);

  // ----------------------------------------------------------------------
  // Render
  // ----------------------------------------------------------------------

  if (loading) {
    return (
      <section>
        <h1 className="text-2xl font-semibold mb-2">Banker Dashboard</h1>
        <p className="text-gray-600">Loading the review workflow…</p>
      </section>
    );
  }

  if (loadError || !checklist || !reviewState) {
    return (
      <section>
        <h1 className="text-2xl font-semibold mb-2">Banker Dashboard</h1>
        <div className="mt-4 rounded border border-red-300 bg-red-50 p-3 text-red-800">
          <p className="font-medium">We could not load the dashboard.</p>
          <p className="text-sm mt-1">{loadError ?? "No data returned."}</p>
          <button
            type="button"
            onClick={() => void loadAll()}
            className="mt-2 rounded bg-red-600 px-3 py-1 text-sm text-white hover:bg-red-700"
          >
            Try again
          </button>
        </div>
      </section>
    );
  }

  const totalBlockers = activeEntries.filter((e) => e.severity === "blocker").length;
  const certifiedBlockers = totalBlockers - uncertifiedBlockers.length;

  return (
    <section className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold">Banker Dashboard</h1>
        <p className="text-gray-600 mt-1 max-w-3xl">
          Review each section, then certify it. The exchange-ready package
          unlocks the moment every blocker section is certified — that lock
          is your due-diligence guarantee.
        </p>
        <p className="text-sm text-gray-500 mt-2">
          Blocker sections certified:{" "}
          <span className="font-semibold text-gray-800">
            {certifiedBlockers} / {totalBlockers}
          </span>
        </p>
      </header>

      {/* --- Section review table --------------------------------------- */}
      <div>
        <h2 className="text-lg font-semibold mb-2">Sections</h2>
        <div className="overflow-x-auto rounded border border-slate-200">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-600">
              <tr>
                <th className="px-3 py-2 font-medium">Title</th>
                <th className="px-3 py-2 font-medium">Section</th>
                <th className="px-3 py-2 font-medium">Severity</th>
                <th className="px-3 py-2 font-medium">State</th>
                <th className="px-3 py-2 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {activeEntries.map((entry) => {
                const state: SectionState =
                  reviewState.states[entry.id] ?? "draft";
                const busy = Boolean(rowBusy[entry.id]);
                const rowErr = rowError[entry.id];
                const emphasize = entry.severity === "blocker";
                const noText = !sectionsWithText.has(entry.id);
                const nxt = nextState(state);
                return (
                  <tr
                    key={entry.id}
                    className={
                      emphasize
                        ? "border-t border-slate-200 bg-red-50/40"
                        : "border-t border-slate-200"
                    }
                  >
                    <td className="px-3 py-2">
                      <div className="font-medium text-slate-800">
                        {entry.title}
                      </div>
                      <div className="text-xs text-slate-500">
                        {entry.clause_ref}
                      </div>
                      {noText && (
                        <div className="text-xs text-amber-700 mt-1">
                          No generated text yet — run generation first.
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-slate-700">{entry.section}</td>
                    <td className="px-3 py-2">
                      <span
                        className={
                          "inline-block rounded px-2 py-0.5 text-xs font-medium " +
                          SEVERITY_BADGE[entry.severity]
                        }
                      >
                        {entry.severity}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={
                          "inline-block rounded px-2 py-0.5 text-xs font-medium " +
                          STATE_BADGE[state]
                        }
                      >
                        {state}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      {nxt ? (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void onAdvance(entry, state)}
                          className={
                            "rounded px-3 py-1 text-xs font-medium text-white " +
                            (busy
                              ? "bg-slate-400 cursor-not-allowed"
                              : "bg-slate-800 hover:bg-slate-900")
                          }
                        >
                          {busy ? "Working…" : nextStateLabel(state)}
                        </button>
                      ) : (
                        <span className="text-xs text-emerald-700">
                          Certified
                        </span>
                      )}
                      {rowErr && (
                        <div className="text-xs text-red-700 mt-1">{rowErr}</div>
                      )}
                    </td>
                  </tr>
                );
              })}
              {activeEntries.length === 0 && (
                <tr>
                  <td
                    className="px-3 py-4 text-center text-slate-500"
                    colSpan={5}
                  >
                    No active checklist entries. Everything is stubbed for now.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* --- Edit form + audit trail ------------------------------------ */}
      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <h2 className="text-lg font-semibold mb-2">Record an edit</h2>
          <p className="text-xs text-slate-500 mb-3">
            Editing a section drops it back to <strong>draft</strong>. Re-review
            and re-certify before exporting — the audit trail records who
            changed what, and when.
          </p>
          <form onSubmit={onSubmitEdit} className="space-y-3">
            <div>
              <label
                className="block text-xs font-medium text-slate-600"
                htmlFor="edit-entry"
              >
                Entry
              </label>
              <select
                id="edit-entry"
                value={editForm.entry_id}
                onChange={(e) =>
                  setEditForm((prev) => ({ ...prev, entry_id: e.target.value }))
                }
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="">Select a section…</option>
                {activeEntries.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.section} — {entry.title}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                className="block text-xs font-medium text-slate-600"
                htmlFor="edit-editor"
              >
                Your name
              </label>
              <input
                id="edit-editor"
                type="text"
                value={editForm.editor}
                onChange={(e) =>
                  setEditForm((prev) => ({ ...prev, editor: e.target.value }))
                }
                placeholder="e.g. R. Iyer, Lead Manager"
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              />
            </div>
            <div>
              <label
                className="block text-xs font-medium text-slate-600"
                htmlFor="edit-before"
              >
                Original text (before)
              </label>
              <textarea
                id="edit-before"
                value={editForm.before}
                onChange={(e) =>
                  setEditForm((prev) => ({ ...prev, before: e.target.value }))
                }
                rows={3}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              />
            </div>
            <div>
              <label
                className="block text-xs font-medium text-slate-600"
                htmlFor="edit-after"
              >
                Replacement text (after)
              </label>
              <textarea
                id="edit-after"
                value={editForm.after}
                onChange={(e) =>
                  setEditForm((prev) => ({ ...prev, after: e.target.value }))
                }
                rows={3}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              />
            </div>
            <button
              type="submit"
              disabled={editBusy}
              className={
                "rounded px-4 py-1.5 text-sm font-medium text-white " +
                (editBusy
                  ? "bg-slate-400 cursor-not-allowed"
                  : "bg-blue-700 hover:bg-blue-800")
              }
            >
              {editBusy ? "Recording…" : "Record edit"}
            </button>
            {editError && (
              <p className="text-sm text-red-700">{editError}</p>
            )}
            {editNotice && (
              <p className="text-sm text-amber-800">{editNotice}</p>
            )}
          </form>
        </div>

        <div>
          <h2 className="text-lg font-semibold mb-2">Audit trail</h2>
          {reviewState.audit_trail.length === 0 ? (
            <p className="text-sm text-slate-500">
              No edits recorded yet. Every banker change will appear here with
              its timestamp and before/after text.
            </p>
          ) : (
            <ul className="space-y-3">
              {reviewState.audit_trail
                .slice()
                .reverse()
                .map((edit, idx) => (
                  <li
                    key={`${edit.entry_id}-${edit.at}-${idx}`}
                    className="rounded border border-slate-200 p-3 text-sm"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="font-medium text-slate-800">
                        {edit.editor}
                      </span>
                      <span className="text-xs text-slate-500">{edit.at}</span>
                    </div>
                    <div className="text-xs text-slate-500 mt-0.5">
                      {edit.entry_id}
                    </div>
                    <div className="mt-2 grid gap-2 md:grid-cols-2">
                      <div>
                        <div className="text-xs font-medium text-slate-600">
                          Before
                        </div>
                        <div className="whitespace-pre-wrap text-xs text-slate-700 bg-slate-50 rounded p-2">
                          {edit.before || "(empty)"}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs font-medium text-slate-600">
                          After
                        </div>
                        <div className="whitespace-pre-wrap text-xs text-slate-700 bg-slate-50 rounded p-2">
                          {edit.after || "(empty)"}
                        </div>
                      </div>
                    </div>
                  </li>
                ))}
            </ul>
          )}
        </div>
      </div>

      {/* --- Export + certification lock -------------------------------- */}
      <div>
        <h2 className="text-lg font-semibold mb-2">Export exchange-ready package</h2>
        <p className="text-sm text-slate-600 max-w-3xl">
          The lock is the promise: no draft leaves this dashboard until every
          blocker section is certified. If any are still pending, we will tell
          you exactly which ones.
        </p>
        <button
          type="button"
          onClick={() => void onExport()}
          disabled={exporting}
          className={
            "mt-3 rounded px-4 py-2 text-sm font-medium text-white " +
            (exporting
              ? "bg-slate-400 cursor-not-allowed"
              : "bg-emerald-700 hover:bg-emerald-800")
          }
        >
          {exporting ? "Preparing package…" : "Export DRHP + abridged prospectus"}
        </button>

        {exportLockMessage && (
          <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3">
            <p className="text-sm font-medium text-amber-900">
              {exportLockMessage}
            </p>
            {uncertifiedBlockers.length > 0 && (
              <ul className="mt-2 list-disc pl-5 text-sm text-amber-900">
                {uncertifiedBlockers.map((entry) => {
                  const state =
                    reviewState.states[entry.id] ?? "draft";
                  return (
                    <li key={entry.id}>
                      <span className="font-medium">{entry.title}</span>
                      <span className="text-amber-800">
                        {" "}
                        — {entry.section} · currently {state}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        {exportError && (
          <div className="mt-3 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
            {exportError}
          </div>
        )}

        {exportResult && (
          <div className="mt-3 rounded border border-emerald-300 bg-emerald-50 p-3">
            <p className="text-sm font-medium text-emerald-900">
              Package ready. Download the documents below.
            </p>
            <ul className="mt-2 space-y-1 text-sm">
              <li>
                <a
                  href={exportResult.drhp}
                  className="text-emerald-800 underline hover:text-emerald-900"
                  download
                >
                  Draft Red Herring Prospectus (DRHP)
                </a>
              </li>
              <li>
                <a
                  href={exportResult.abridged}
                  className="text-emerald-800 underline hover:text-emerald-900"
                  download
                >
                  Draft abridged prospectus
                </a>
              </li>
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
