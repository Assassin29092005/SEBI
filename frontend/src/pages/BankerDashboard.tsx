import { useCallback, useEffect, useMemo, useState } from "react";
import {
  advanceSection,
  acceptProposal,
  apiDownloadFile,
  bundleUrl,
  confirmFact,
  exportPackage,
  getArithmetic,
  getAuditLog,
  getContradictions,
  getExtractionReliability,
  getRegulatoryWatchStatus,
  getReviewState,
  getSchema,
  getSections,
  postRegulatoryWatchCheck,
  recordEdit,
  uploadExtract,
  type AuditEvent,
  type AuditOutcome,
  type BankerEdit,
  type Checklist,
  type ChecklistEntry,
  type ExportResponse,
  type ExtractionProposal,
  type ExtractionReliabilityReport,
  type GeneratedSection,
  type ReviewState,
  type SectionState,
  type Severity,
  type StalenessCheckResult,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import TextDiff from "../components/TextDiff";

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
  before: string;
  after: string;
}

const EMPTY_EDIT: EditFormState = {
  entry_id: "",
  before: "",
  after: "",
};

// --------------------------------------------------------------------------
// Due-diligence certificate upload (banker role)
// --------------------------------------------------------------------------

interface DueDiligenceUploadProps {
  reviewState: ReviewState | null;
  onUploadComplete: () => void;
}

type ProposalStage = "pending" | "accepting" | "confirming" | "confirmed" | "rejected" | "error";

interface ProposalState {
  stage: ProposalStage;
  error?: string;
}

function DueDiligenceUpload({ reviewState, onUploadComplete }: DueDiligenceUploadProps) {
  const [uploading, setUploading] = useState<boolean>(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [proposals, setProposals] = useState<ExtractionProposal[]>([]);
  const [proposalStates, setProposalStates] = useState<Record<number, ProposalState>>({});
  const [certified, setCertified] = useState<boolean>(false);

  // Check if already certified in review state
  useEffect(() => {
    if (reviewState?.states["certification.due_diligence_certificate"] === "certified") {
      setCertified(true);
    }
  }, [reviewState]);

  const handleUpload = async (file: File) => {
    setUploading(true);
    setUploadError(null);
    setProposals([]);
    setProposalStates({});
    try {
      // uploadExtract() (not a bare fetch) so the Authorization header goes
      // out with the request — every endpoint requires it now (see app.auth).
      const data = await uploadExtract(file);
      setProposals(data);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleAccept = async (idx: number, proposal: ExtractionProposal) => {
    setProposalStates((prev) => ({ ...prev, [idx]: { stage: "accepting" } }));
    try {
      const fact = await acceptProposal(proposal, "banker");
      setProposalStates((prev) => ({ ...prev, [idx]: { stage: "confirming" } }));
      await confirmFact(fact.fact_id);
      setProposalStates((prev) => ({ ...prev, [idx]: { stage: "confirmed" } }));
      onUploadComplete(); // refresh review state
    } catch (err) {
      setProposalStates((prev) => ({ ...prev, [idx]: { stage: "error", error: err instanceof Error ? err.message : "Failed" } }));
    }
  };

  const handleReject = (idx: number) => {
    setProposalStates((prev) => ({ ...prev, [idx]: { stage: "rejected" } }));
  };

  if (certified) {
    return (
      <div className="rounded bg-emerald-50 border border-emerald-200 p-4">
        <div className="flex items-center gap-2">
          <svg className="h-5 w-5 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
          <span className="font-medium text-emerald-900">Due-diligence certificate already certified</span>
        </div>
        <p className="text-sm text-emerald-800 mt-1">
          This section is certified in the review workflow. No further upload needed.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <label className="inline-flex items-center gap-3 cursor-pointer">
        <span className="rounded bg-blue-600 text-white text-sm px-3 py-1.5 hover:bg-blue-700">
          Choose file
        </span>
        <input
          type="file"
          className="hidden"
          disabled={uploading}
          accept=".pdf,.txt,.png,.jpg,.jpeg,.tiff,.tif,.bmp,.webp"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleUpload(file);
            e.target.value = "";
          }}
        />
        {uploading && <span className="text-sm text-slate-500">Extracting…</span>}
        {uploadError && (
          <span className="text-sm text-red-700">Error: {uploadError}</span>
        )}
      </label>

      {proposals.length > 0 && (
        <div className="space-y-3">
          {proposals.map((p, idx) => {
            const state = proposalStates[idx] ?? { stage: "pending" };
            const isMoney = p.fact_key.endsWith("_paise");
            const displayValue =
              isMoney && typeof p.value === "number" && Number.isInteger(p.value)
                ? `₹${(p.value / 100).toLocaleString("en-IN")}`
                : String(p.value);

            return (
              <div key={`${p.fact_key}-${idx}`} className="rounded border bg-white p-4 shadow-sm">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div>
                    <p className="text-xs uppercase tracking-wide text-slate-500">{p.fact_key}</p>
                    <p className="text-lg font-semibold text-slate-900">{displayValue}</p>
                  </div>
                  <span className={`shrink-0 rounded-full text-xs px-2 py-0.5 ${
                    state.stage === "confirmed" ? "bg-emerald-100 text-emerald-800" :
                    state.stage === "rejected" ? "bg-slate-100 text-slate-500" :
                    state.stage === "error" ? "bg-red-100 text-red-800" :
                    "bg-amber-100 text-amber-800"
                  }`}>
                    {state.stage === "pending" ? "Pending" :
                     state.stage === "accepting" ? "Accepting…" :
                     state.stage === "confirming" ? "Confirming…" :
                     state.stage === "confirmed" ? "Confirmed" :
                     state.stage === "rejected" ? "Rejected" : "Error"}
                  </span>
                </div>

                <div className="rounded bg-yellow-50 border border-yellow-200 p-3 text-sm text-slate-800">
                  <p className="mb-1 text-xs text-slate-600">
                    Found on page {p.page} · {p.source_file}
                  </p>
                  <p className="italic">"{p.snippet}"</p>
                </div>

                {state.stage === "confirmed" && (
                  <p className="mt-3 text-sm text-emerald-800">Confirmed and added to fact store.</p>
                )}
                {state.stage === "rejected" && (
                  <p className="mt-3 text-sm text-slate-500">Discarded. This value will not enter the draft.</p>
                )}
                {state.stage === "error" && state.error && (
                  <p className="mt-3 text-sm text-red-700">Error: {state.error}</p>
                )}

                {(() => {
                  const st = state.stage as ProposalStage;
                  if (st !== "pending" && st !== "error") return null;
                  return (
                    <div className="mt-3 flex gap-2">
                      <button
                        type="button"
                        onClick={() => handleAccept(idx, p)}
                        // @ts-ignore: TypeScript narrows state.stage in outer scope
                        disabled={st === "accepting" || st === "confirming"}
                        className="rounded bg-emerald-600 text-white text-sm px-3 py-1.5 hover:bg-emerald-700 disabled:bg-emerald-400"
                      >
                        Confirm this fact
                      </button>
                      <button
                        type="button"
                        onClick={() => handleReject(idx)}
                        // @ts-ignore: TypeScript narrows state.stage in outer scope
                        disabled={st === "accepting" || st === "confirming"}
                        className="rounded border text-sm px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        Reject
                      </button>
                    </div>
                  );
                })()}
                {state.stage === "accepting" && <p className="mt-3 text-sm text-slate-500">Accepting…</p>}
                {state.stage === "confirming" && <p className="mt-3 text-sm text-slate-500">Confirming…</p>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Access log (banker-only — see backend/app/audit). Fact provenance already
// answers "who supplied this value" and the review trail answers "who edited
// this section"; this is the third question a compliance review asks — "who
// *looked* at it" — and until now it was API-only with no way to read it
// without curl.
//
// Loaded on an explicit click rather than on mount: unlike the panels above
// it is a potentially large table nobody needs on every dashboard visit, and
// reading the access log is itself an audited action.
// --------------------------------------------------------------------------

const OUTCOME_BADGE: Record<AuditOutcome, string> = {
  success: "bg-green-50 text-green-800 border-green-200",
  denied: "bg-red-50 text-red-800 border-red-200",
  error: "bg-amber-50 text-amber-800 border-amber-200",
};

const AUDIT_PAGE_SIZE = 100;

function formatAuditTime(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

function AuditLogPanel() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actorEmail, setActorEmail] = useState("");
  const [outcome, setOutcome] = useState<AuditOutcome | "">("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setEvents(
        await getAuditLog({
          actor_email: actorEmail.trim() || undefined,
          outcome: outcome || undefined,
          limit: AUDIT_PAGE_SIZE,
        }),
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [actorEmail, outcome]);

  const deniedCount = useMemo(
    () => (events ?? []).filter((e) => e.outcome === "denied").length,
    [events],
  );

  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <h2 className="text-lg font-semibold mb-1">Access log</h2>
      <p className="text-sm text-slate-600 mb-3 max-w-3xl">
        Every API request against this deployment — who, what, when, and
        whether it succeeded. Denied attempts are recorded too, so this also
        answers &quot;did anyone try something they shouldn&apos;t have.&quot;
        Static assets and health checks are excluded server-side; they say
        nothing about who read which fact.
      </p>

      <div className="flex flex-wrap items-end gap-2 mb-3">
        <label className="flex flex-col text-xs text-slate-600">
          Actor email
          <input
            type="text"
            value={actorEmail}
            onChange={(e) => setActorEmail(e.target.value)}
            placeholder="anyone"
            className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm focus:ring-2"
          />
        </label>
        <label className="flex flex-col text-xs text-slate-600">
          Outcome
          <select
            value={outcome}
            onChange={(e) => setOutcome(e.target.value as AuditOutcome | "")}
            className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm focus:ring-2"
          >
            <option value="">any</option>
            <option value="success">success</option>
            <option value="denied">denied</option>
            <option value="error">error</option>
          </select>
        </label>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {loading ? "Loading…" : events ? "Refresh" : "Load access log"}
        </button>
      </div>

      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}

      {events && (
        <>
          <p className="text-xs text-slate-500 mb-2">
            Showing the {events.length} most recent
            {events.length === AUDIT_PAGE_SIZE ? ` of possibly more` : ""}
            {deniedCount > 0 ? ` · ${deniedCount} denied` : ""}
          </p>
          {events.length === 0 ? (
            <p className="text-sm text-slate-600">No events match those filters.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-xs">
                <thead className="text-slate-500">
                  <tr>
                    <th scope="col" className="py-1 pr-3 font-medium">When</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Actor</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Action</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Resource</th>
                    <th scope="col" className="py-1 pr-3 font-medium">Outcome</th>
                  </tr>
                </thead>
                <tbody className="text-slate-800">
                  {events.map((e) => (
                    <tr key={e.event_id} className="border-t border-slate-100 align-top">
                      <td className="py-1 pr-3 whitespace-nowrap">{formatAuditTime(e.at)}</td>
                      <td className="py-1 pr-3">
                        {e.actor_email}
                        {e.actor_role ? (
                          <span className="text-slate-500"> ({e.actor_role})</span>
                        ) : null}
                      </td>
                      <td className="py-1 pr-3 font-mono break-all">{e.action}</td>
                      <td className="py-1 pr-3">
                        {e.resource_type}
                        {e.resource_id ? (
                          <span className="text-slate-500 font-mono break-all">
                            {" "}
                            {e.resource_id}
                          </span>
                        ) : null}
                      </td>
                      <td className="py-1 pr-3 whitespace-nowrap">
                        <span
                          className={
                            "inline-block rounded border px-1.5 py-0.5 " +
                            OUTCOME_BADGE[e.outcome]
                          }
                        >
                          {e.outcome} {e.status_code}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Extraction reliability (banker-only feedback-loop signal — see
// backend/app/extraction_reliability). Every correction a banker or
// promoter makes to an extracted fact already gets recorded with who did
// it (Fact.corrected_by_role); this panel is the first place that history
// turns into a QA signal instead of sitting invisibly in the fact chain.
// --------------------------------------------------------------------------

function formatPct(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}

function ExtractionReliabilityPanel({
  report,
}: {
  report: ExtractionReliabilityReport;
}) {
  const hasData = report.total_extracted_facts > 0;

  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <h2 className="text-lg font-semibold mb-1">Extraction reliability</h2>
      <p className="text-sm text-slate-600 mb-4 max-w-3xl">
        How often extracted facts (from a document, lookup, or role upload —
        not a promoter typing an answer directly) turned out to need a
        correction, and how often it was a banker&apos;s due-diligence review
        that caught it rather than a self-correction. Builds up as real
        corrections accumulate — sparse or empty on a fresh draft.
      </p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 mb-4">
        <div className="rounded border border-slate-200 bg-slate-50 p-3">
          <div className="text-xs uppercase tracking-wide text-slate-500">
            Extracted facts tracked
          </div>
          <div className="text-2xl font-semibold text-slate-900">
            {report.total_extracted_facts}
          </div>
        </div>
        <div className="rounded border border-slate-200 bg-slate-50 p-3">
          <div className="text-xs uppercase tracking-wide text-slate-500">
            Total corrections
          </div>
          <div className="text-2xl font-semibold text-slate-900">
            {report.total_corrections}
          </div>
        </div>
        <div className="rounded border border-amber-200 bg-amber-50 p-3">
          <div className="text-xs uppercase tracking-wide text-amber-700">
            Caught by banker due diligence
          </div>
          <div className="text-2xl font-semibold text-amber-900">
            {report.total_banker_caught_corrections}
          </div>
        </div>
      </div>

      {!hasData ? (
        <p className="text-sm text-slate-500">
          No extracted facts yet — this fills in once documents/uploads/
          lookups start feeding the fact store.
        </p>
      ) : (
        <div className="overflow-x-auto rounded border border-slate-200">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-600">
              <tr>
                <th className="px-3 py-2 font-medium">Source</th>
                <th className="px-3 py-2 font-medium">Confidence</th>
                <th className="px-3 py-2 font-medium">Facts</th>
                <th className="px-3 py-2 font-medium">Corrected</th>
                <th className="px-3 py-2 font-medium">Correction rate</th>
                <th className="px-3 py-2 font-medium">Banker-caught</th>
              </tr>
            </thead>
            <tbody>
              {report.buckets.map((b) => (
                <tr
                  key={`${b.provenance_kind}-${b.confidence_band}`}
                  className="border-t border-slate-200"
                >
                  <td className="px-3 py-2 text-slate-700">{b.provenance_kind}</td>
                  <td className="px-3 py-2 text-slate-700">{b.confidence_band}</td>
                  <td className="px-3 py-2 text-slate-700">{b.total_facts}</td>
                  <td className="px-3 py-2 text-slate-700">{b.corrected_count}</td>
                  <td className="px-3 py-2 text-slate-700">{formatPct(b.correction_rate)}</td>
                  <td className="px-3 py-2 text-slate-700">
                    {b.banker_caught_count > 0 ? (
                      <span className="rounded-full bg-amber-100 text-amber-800 px-2 py-0.5 text-xs font-medium">
                        {b.banker_caught_count}
                      </span>
                    ) : (
                      "0"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Regulatory staleness watcher (see backend/app/regulatory_watch.py). The
// schema pins an exact ICDR amendment date; this compares that pin against
// SEBI's real, live ICDR-tagged postings on demand — a real external HTTP
// call, so it only ever fires on an explicit banker click, never silently
// on page load (status alone, which just reads the last cached result, IS
// loaded automatically — see the effect in BankerDashboard below).
// --------------------------------------------------------------------------

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function RegulatoryWatchPanel({
  result,
  checking,
  onCheckNow,
}: {
  result: StalenessCheckResult | null;
  checking: boolean;
  onCheckNow: () => void;
}) {
  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <h2 className="text-lg font-semibold">Regulatory staleness</h2>
        <button
          type="button"
          onClick={onCheckNow}
          disabled={checking}
          className={
            "rounded px-3 py-1.5 text-xs font-medium text-white " +
            (checking ? "bg-slate-400 cursor-not-allowed" : "bg-slate-800 hover:bg-slate-900")
          }
        >
          {checking ? "Checking sebi.gov.in…" : "Check for updates now"}
        </button>
      </div>
      <p className="text-sm text-slate-600 mb-4 max-w-3xl">
        The checklist schema pins an exact ICDR amendment date. A demo checks
        that pin once; production doesn&apos;t get that luxury — this compares
        it against SEBI&apos;s real, live ICDR-tagged postings (circulars,
        consultation papers, informal guidance, enforcement notices) on
        demand. Never auto-updates the schema — every schema change is
        human-reviewed; this only flags &ldquo;go check this.&rdquo;
      </p>

      {!result ? (
        <p className="text-sm text-slate-500">
          Not checked yet this session. Click &ldquo;Check for updates
          now&rdquo; to compare the pinned date against SEBI&apos;s site.
        </p>
      ) : !result.checked_successfully ? (
        <div className="rounded border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
          Could not reach SEBI&apos;s site (or its layout changed) as of{" "}
          {formatDateTime(result.checked_at)}. This is inconclusive, not a
          clean bill of health — try again later.
        </div>
      ) : result.newer_updates.length === 0 ? (
        <div className="rounded border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
          Clean as of {formatDateTime(result.checked_at)}: nothing newer than
          the pinned {result.pinned_amended_through} found ({result.source}).
        </div>
      ) : (
        <div>
          <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 mb-3">
            {result.newer_updates.length} ICDR-tagged posting
            {result.newer_updates.length === 1 ? "" : "s"} newer than the
            pinned {result.pinned_amended_through} (checked{" "}
            {formatDateTime(result.checked_at)}). Most of these will be
            consultation papers or guidance, not notified amendments — a
            human should go check, this is not itself a claim the schema is
            out of date.
          </div>
          <ul className="space-y-2">
            {result.newer_updates.map((u) => (
              <li key={u.url} className="rounded border border-slate-200 p-3 text-sm">
                <div className="flex items-baseline justify-between gap-3">
                  <a
                    href={u.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-700 hover:text-blue-900 underline font-medium"
                  >
                    {u.title}
                  </a>
                  <span className="shrink-0 text-xs text-slate-500">{u.published}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function BankerDashboard() {
  // The server always records the authenticated banker as editor (see
  // POST /api/review/edit) — no free-text "editor" field to spoof or forget.
  const { user } = useAuth();
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

  // Open validation findings (contradictions + arithmetic). Fetched on mount,
  // strictly non-blocking: if either endpoint fails this stays null and the
  // summary card simply does not render — the certification workflow must
  // never depend on the validation endpoints being up.
  const [findingCounts, setFindingCounts] = useState<{
    contradictions: number;
    arithmetic: number;
  } | null>(null);

  // Extraction reliability report — banker-only, fetched the same
  // non-blocking way as findingCounts above: the certification workflow
  // must never depend on this endpoint being up.
  const [reliabilityReport, setReliabilityReport] =
    useState<ExtractionReliabilityReport | null>(null);

  // Regulatory staleness — the STATUS read (last cached result, or null) is
  // safe to auto-load like the panels above; the actual CHECK is a real
  // external HTTP call and only ever runs on an explicit click (see
  // onRegulatoryWatchCheck below).
  const [staleness, setStaleness] = useState<StalenessCheckResult | null>(null);
  const [checkingStaleness, setCheckingStaleness] = useState<boolean>(false);

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

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [contradictions, arithmetic] = await Promise.all([
          getContradictions(),
          getArithmetic(),
        ]);
        if (!cancelled) {
          setFindingCounts({
            contradictions: contradictions.length,
            arithmetic: arithmetic.length,
          });
        }
      } catch {
        // Deliberately swallowed — see the findingCounts state comment.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const report = await getExtractionReliability();
        if (!cancelled) setReliabilityReport(report);
      } catch {
        // Deliberately swallowed — see the reliabilityReport state comment.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await getRegulatoryWatchStatus();
        if (!cancelled) setStaleness(result);
      } catch {
        // Deliberately swallowed — a status read failing shouldn't disturb
        // the rest of the dashboard; the panel just stays in its
        // "not checked yet" state.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onRegulatoryWatchCheck = useCallback(async () => {
    setCheckingStaleness(true);
    try {
      const result = await postRegulatoryWatchCheck();
      setStaleness(result);
    } catch {
      // The panel keeps showing whatever it last had; a transient failure
      // here (e.g. a 403 if role state ever changed mid-session) shouldn't
      // crash the dashboard.
    } finally {
      setCheckingStaleness(false);
    }
  }, []);

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
      if (!editForm.entry_id || !editForm.after) {
        setEditError("Entry and the replacement text are required.");
        return;
      }
      setEditBusy(true);
      setEditError(null);
      setEditNotice(null);
      try {
        const edit: BankerEdit = {
          entry_id: editForm.entry_id,
          // The server overwrites this with the authenticated banker's email
          // regardless of what's sent — see POST /api/review/edit.
          editor: user?.email ?? "",
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
        setEditForm(EMPTY_EDIT);
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
  // Same lock knowledge the backend enforces with a 409 on the bundle
  // endpoint: every blocker-severity section must be certified.
  const bundleLocked = uncertifiedBlockers.length > 0;
  const openFindings = findingCounts
    ? findingCounts.contradictions + findingCounts.arithmetic
    : 0;

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

      {/* --- Validation summary: outstanding findings before certifying -- */}
      {findingCounts && (
        <div
          className={
            "rounded border p-3 " +
            (openFindings > 0
              ? "border-red-300 bg-red-50"
              : "border-emerald-200 bg-emerald-50")
          }
        >
          <p
            className={
              "text-sm font-semibold " +
              (openFindings > 0 ? "text-red-800" : "text-emerald-900")
            }
          >
            {`Open findings: ${findingCounts.contradictions} contradiction${
              findingCounts.contradictions === 1 ? "" : "s"
            }, ${findingCounts.arithmetic} arithmetic`}
          </p>
          <p
            className={
              "mt-1 text-xs " +
              (openFindings > 0 ? "text-red-700" : "text-emerald-800")
            }
          >
            {openFindings > 0
              ? "Outstanding validation findings — review them before certifying sections."
              : "The contradiction and arithmetic checks are clean."}
          </p>
        </div>
      )}

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
          <p className="text-xs text-slate-500 mb-3">
            Editing as{" "}
            <span className="font-medium text-slate-700">
              {user?.name} ({user?.email})
            </span>
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
                    <TextDiff before={edit.before} after={edit.after} />
                  </li>
                ))}
            </ul>
          )}
        </div>
      </div>

      {/* --- Due-diligence certificate upload (banker role) --------------- */}
      <div className="rounded border border-slate-200 bg-white p-4">
        <h2 className="text-lg font-semibold mb-2">Due-diligence certificate</h2>
        <p className="text-sm text-slate-600 mb-4">
          Upload the lead manager&apos;s due-diligence certificate (Form A / Form G
          with site visit report) as required by ICDR Reg. 246(3). This is a
          blocker-severity requirement — the certification lock cannot be cleared
          without it.
        </p>
        <DueDiligenceUpload
          reviewState={reviewState}
          onUploadComplete={loadAll}
        />
      </div>

      {/* --- Extraction reliability (feedback-loop signal) --------------- */}
      {reliabilityReport && (
        <ExtractionReliabilityPanel report={reliabilityReport} />
      )}

      {/* --- Access log (who looked at what) ----------------------------- */}
      <AuditLogPanel />


      {/* --- Regulatory staleness watcher --------------------------------- */}
      <RegulatoryWatchPanel
        result={staleness}
        checking={checkingStaleness}
        onCheckNow={() => void onRegulatoryWatchCheck()}
      />

      {/* --- Export + certification lock -------------------------------- */}
      <div>
        <h2 className="text-lg font-semibold mb-2">Export exchange-ready package</h2>
        <p className="text-sm text-slate-600 max-w-3xl">
          The lock is the promise: no draft leaves this dashboard until every
          blocker section is certified. If any are still pending, we will tell
          you exactly which ones.
        </p>
        <div className="mt-3 flex flex-wrap items-start gap-3">
          <button
            type="button"
            onClick={() => void onExport()}
            disabled={exporting}
            className={
              "rounded px-4 py-2 text-sm font-medium text-white " +
              (exporting
                ? "bg-slate-400 cursor-not-allowed"
                : "bg-emerald-700 hover:bg-emerald-800")
            }
          >
            {exporting ? "Preparing package…" : "Export DRHP + abridged prospectus"}
          </button>

          {/* Bundle download. GET /api/export/bundle answers 409 while the
              certification lock is engaged, so the anchor only goes live when
              the same client-side lock computation says every blocker is
              certified — a disabled button never triggers a broken download. */}
          <div className="max-w-md">
            {bundleLocked ? (
              <>
                <button
                  type="button"
                  disabled
                  className="rounded bg-slate-400 px-4 py-2 text-sm font-medium text-white cursor-not-allowed"
                >
                  Download exchange-ready package (.zip)
                </button>
                <p className="mt-1 text-xs font-medium text-amber-800">
                  {`Certification lock: ${uncertifiedBlockers.length} blocker section${
                    uncertifiedBlockers.length === 1 ? "" : "s"
                  } uncertified — the package unlocks when every blocker is certified.`}
                </p>
              </>
            ) : (
              <button
                type="button"
                onClick={() => void apiDownloadFile(bundleUrl(), "exchange_ready_package.zip")}
                className="inline-block rounded bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-800"
              >
                Download exchange-ready package (.zip)
              </button>
            )}
            <p className="mt-1 text-xs text-slate-500">
              Contains both draft documents, the gap report, contradiction and
              arithmetic findings, examiner objections, the full fact
              provenance ledger, and the review audit trail.
            </p>
          </div>
        </div>

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
                <button
                  type="button"
                  onClick={() => void apiDownloadFile(exportResult.drhp, "drhp.docx")}
                  className="text-emerald-800 underline hover:text-emerald-900 text-sm"
                >
                  Draft Red Herring Prospectus (DRHP)
                </button>
              </li>
              <li>
                <button
                  type="button"
                  onClick={() => void apiDownloadFile(exportResult.abridged, "abridged_prospectus.docx")}
                  className="text-emerald-800 underline hover:text-emerald-900 text-sm"
                >
                  Draft abridged prospectus
                </button>
              </li>
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
