import { useCallback, useEffect, useMemo, useState } from "react";
import { getGaps } from "../api/client";
import type { Gap, GapReport as GapReportPayload, Role, Severity } from "../api/client";

// --------------------------------------------------------------------------
// Role + severity presentation helpers. Copy is promoter-first plain English.
// --------------------------------------------------------------------------

const ROLE_ORDER: Role[] = ["promoter", "auditor", "banker", "system"];

const ROLE_HEADERS: Record<Role, string> = {
  promoter: "You can fix these",
  auditor: "Needs your auditor",
  banker: "Needs your merchant banker",
  system: "System items",
};

const ROLE_BLURBS: Record<Role, string> = {
  promoter:
    "Answer these questions in the wizard or upload a document that has the answer.",
  auditor:
    "Your peer-reviewed auditor has to prepare and sign these — the tool ingests and formats them but never writes them.",
  banker:
    "Your merchant banker (Lead Manager) has to supply these as part of their due diligence.",
  system: "Housekeeping items handled by the tool.",
};

const SEVERITY_ORDER: Severity[] = ["blocker", "material", "minor"];

const SEVERITY_LABELS: Record<Severity, string> = {
  blocker: "Blocker",
  material: "Material",
  minor: "Minor",
};

const SEVERITY_BADGE: Record<Severity, string> = {
  blocker: "bg-red-100 text-red-800 border-red-200",
  material: "bg-amber-100 text-amber-800 border-amber-200",
  minor: "bg-gray-100 text-gray-700 border-gray-200",
};

const SEVERITY_STRIP: Record<Severity, string> = {
  blocker: "bg-red-50 text-red-800 border-red-200",
  material: "bg-amber-50 text-amber-800 border-amber-200",
  minor: "bg-gray-50 text-gray-700 border-gray-200",
};

// --------------------------------------------------------------------------
// Humanise ontology keys like ``share_allotments[].issue_price_paise`` into
// "Share allotments — issue price (INR)". Purely cosmetic; the raw key stays
// available in the DOM as a title tooltip so the banker can look it up.
// --------------------------------------------------------------------------

function humaniseFactKey(key: string): string {
  return key
    .split(".")
    .map((part) => {
      const stripped = part.replace(/\[\]/g, "");
      const words = stripped.split("_").filter(Boolean);
      if (words.length === 0) return part;
      const last = words[words.length - 1];
      let suffix = "";
      if (last === "paise") {
        words.pop();
        suffix = " (INR)";
      }
      const sentence = words
        .map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
        .join(" ");
      return sentence + suffix;
    })
    .join(" — ");
}

// --------------------------------------------------------------------------
// Grouping + counting
// --------------------------------------------------------------------------

interface Counts {
  bySeverity: Record<Severity, number>;
  byRole: Record<Role, number>;
}

function tally(gaps: readonly Gap[]): Counts {
  const bySeverity: Record<Severity, number> = { blocker: 0, material: 0, minor: 0 };
  const byRole: Record<Role, number> = { promoter: 0, auditor: 0, banker: 0, system: 0 };
  for (const g of gaps) {
    bySeverity[g.severity] += 1;
    byRole[g.routed_to] += 1;
  }
  return { bySeverity, byRole };
}

function groupByRole(gaps: readonly Gap[]): Record<Role, Gap[]> {
  const out: Record<Role, Gap[]> = { promoter: [], auditor: [], banker: [], system: [] };
  for (const g of gaps) {
    out[g.routed_to].push(g);
  }
  const severityRank: Record<Severity, number> = { blocker: 0, material: 1, minor: 2 };
  for (const role of ROLE_ORDER) {
    out[role].sort((a, b) => {
      const s = severityRank[a.severity] - severityRank[b.severity];
      if (s !== 0) return s;
      const sec = a.section.localeCompare(b.section);
      if (sec !== 0) return sec;
      return a.missing_fact_key.localeCompare(b.missing_fact_key);
    });
  }
  return out;
}

// --------------------------------------------------------------------------
// Sub-components
// --------------------------------------------------------------------------

function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={
        "inline-block rounded-full border px-2 py-0.5 text-xs font-medium " +
        SEVERITY_BADGE[severity]
      }
    >
      {SEVERITY_LABELS[severity]}
    </span>
  );
}

function ClauseChip({ clauseRef }: { clauseRef: string }) {
  return (
    <span
      className="inline-block rounded border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-xs text-slate-700"
      title={clauseRef}
    >
      {clauseRef}
    </span>
  );
}

function GapRow({ gap }: { gap: Gap }) {
  return (
    <li className="flex flex-col gap-1 border-t border-gray-100 py-3 first:border-t-0 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-gray-900">{gap.section}</div>
        <div
          className="mt-0.5 text-sm text-gray-700"
          title={gap.missing_fact_key}
        >
          {humaniseFactKey(gap.missing_fact_key)}
        </div>
        <div className="mt-1 text-xs text-gray-500" title={gap.entry_id}>
          Requirement: <span className="font-mono">{gap.entry_id}</span>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
        <ClauseChip clauseRef={gap.clause_ref} />
        <SeverityBadge severity={gap.severity} />
      </div>
    </li>
  );
}

function SummaryStrip({ counts, total }: { counts: Counts; total: number }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="text-2xl font-semibold text-gray-900">{total}</div>
          <div className="text-xs uppercase tracking-wide text-gray-500">
            {total === 1 ? "open gap" : "open gaps"}
          </div>
        </div>
        <div className="text-xs text-gray-500">
          Each gap is a required disclosure whose fact is missing or unconfirmed.
        </div>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2">
        {SEVERITY_ORDER.map((sev) => (
          <div
            key={sev}
            className={
              "rounded border px-3 py-2 text-center " + SEVERITY_STRIP[sev]
            }
          >
            <div className="text-lg font-semibold">{counts.bySeverity[sev]}</div>
            <div className="text-xs uppercase tracking-wide">
              {SEVERITY_LABELS[sev]}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {ROLE_ORDER.map((role) => (
          <div
            key={role}
            className="rounded border border-gray-200 bg-gray-50 px-3 py-2"
          >
            <div className="text-sm font-semibold text-gray-900">
              {counts.byRole[role]}
            </div>
            <div className="text-xs text-gray-600">{ROLE_HEADERS[role]}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RoleGroup({ role, gaps }: { role: Role; gaps: Gap[] }) {
  if (gaps.length === 0) return null;
  const compact = role === "system";
  return (
    <section
      className={
        "rounded-lg border border-gray-200 bg-white " +
        (compact ? "p-4" : "p-4 sm:p-6")
      }
    >
      <header className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2
          className={
            compact
              ? "text-base font-semibold text-gray-800"
              : "text-lg font-semibold text-gray-900"
          }
        >
          {ROLE_HEADERS[role]}
        </h2>
        <span className="text-xs text-gray-500">
          {gaps.length} {gaps.length === 1 ? "item" : "items"}
        </span>
      </header>
      <p className={"text-sm text-gray-600 " + (compact ? "mb-2" : "mb-4")}>
        {ROLE_BLURBS[role]}
      </p>
      <ul className={compact ? "text-sm" : ""}>
        {gaps.map((g) => (
          <GapRow key={`${g.entry_id}::${g.missing_fact_key}`} gap={g} />
        ))}
      </ul>
    </section>
  );
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; report: GapReportPayload };

export default function GapReport() {
  const [state, setState] = useState<LoadState>({ kind: "idle" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const report = await getGaps();
      setState({ kind: "ready", report });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load gaps.";
      setState({ kind: "error", message });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const grouped = useMemo(() => {
    if (state.kind !== "ready") return null;
    return groupByRole(state.report.gaps);
  }, [state]);

  const counts = useMemo(() => {
    if (state.kind !== "ready") return null;
    return tally(state.report.gaps);
  }, [state]);

  const isLoading = state.kind === "loading";

  return (
    <section className="mx-auto max-w-4xl space-y-6 p-2">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Gap Report</h1>
          <p className="mt-1 max-w-2xl text-gray-600">
            What is still missing from your draft, and who can provide it. Every
            requirement here traces back to an ICDR Chapter IX clause — nothing
            is invented.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={isLoading}
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isLoading ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {state.kind === "loading" && (
        <div
          role="status"
          aria-live="polite"
          className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-600"
        >
          Loading your gap report…
        </div>
      )}

      {state.kind === "error" && (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
        >
          <div className="font-medium">We could not load your gap report.</div>
          <div className="mt-1 text-red-700">{state.message}</div>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-3 rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-800 hover:bg-red-100"
          >
            Try again
          </button>
        </div>
      )}

      {state.kind === "ready" && counts && grouped && (
        <>
          <SummaryStrip counts={counts} total={state.report.gaps.length} />

          {state.report.gaps.length === 0 ? (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-6 text-center">
              <div className="text-lg font-semibold text-emerald-900">
                No gaps — every requirement has a confirmed fact.
              </div>
              <p className="mt-1 text-sm text-emerald-800">
                Your draft is ready for validation and merchant-banker review.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {(["promoter", "auditor", "banker"] as const).map((role) => (
                <RoleGroup key={role} role={role} gaps={grouped[role]} />
              ))}
              <RoleGroup role="system" gaps={grouped.system} />
            </div>
          )}
        </>
      )}
    </section>
  );
}
