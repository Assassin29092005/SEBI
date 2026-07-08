import { useCallback, useEffect, useMemo, useState } from "react";
import {
  assembleUrl,
  formatPaise,
  getArithmetic,
  getBenchmark,
  getBoilerplate,
  getContradictions,
  getCoverage,
  getExaminer,
  getFacts,
  getGaps,
  getSections,
  postGenerate,
  type ArithmeticFinding,
  type BenchmarkReport,
  type BoilerplateFlag,
  type Citation,
  type Contradiction,
  type CoverageReport,
  type Fact,
  type GapReport,
  type GeneratedSection,
  type Objection,
  type ReferenceBenchmark,
  type Role,
  type Severity,
} from "../api/client";

// --------------------------------------------------------------------------
// Small helpers
// --------------------------------------------------------------------------

type LoadState = "idle" | "loading" | "ready" | "error";

interface CitationMarker {
  kind: "citation";
  factId: string;
  ordinal: number;
  start: number;
  end: number;
}

interface RequiresInputMarker {
  kind: "requires";
  start: number;
  end: number;
  label: string;
}

type Marker = CitationMarker | RequiresInputMarker;

interface Segment {
  key: string;
  text: string;
  marker: Marker | null;
}

const REQUIRES_INPUT_RE = /\[REQUIRES INPUT:[^\]]*\]/g;

/**
 * Slice a section's text into a run of plain-text and marker segments.
 * Citation spans and [REQUIRES INPUT: ...] tokens are both marked so we can
 * render them differently in JSX without touching the source string.
 */
function buildSegments(section: GeneratedSection): Segment[] {
  const markers: Marker[] = [];

  section.citations.forEach((c: Citation, idx: number) => {
    const [start, end] = c.text_span;
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      markers.push({
        kind: "citation",
        factId: c.fact_id,
        ordinal: idx + 1,
        start,
        end,
      });
    }
  });

  let match: RegExpExecArray | null;
  const re = new RegExp(REQUIRES_INPUT_RE.source, "g");
  while ((match = re.exec(section.text)) !== null) {
    markers.push({
      kind: "requires",
      start: match.index,
      end: match.index + match[0].length,
      label: match[0],
    });
  }

  markers.sort((a, b) => a.start - b.start);

  // Drop markers that overlap an already-emitted one (keep the earlier).
  const clean: Marker[] = [];
  let cursor = 0;
  for (const m of markers) {
    if (m.start < cursor) continue;
    clean.push(m);
    cursor = m.end;
  }

  const segments: Segment[] = [];
  let pos = 0;
  clean.forEach((m, i) => {
    if (m.start > pos) {
      segments.push({
        key: `t-${section.entry_id}-${i}`,
        text: section.text.slice(pos, m.start),
        marker: null,
      });
    }
    segments.push({
      key: `m-${section.entry_id}-${i}`,
      text: section.text.slice(m.start, m.end),
      marker: m,
    });
    pos = m.end;
  });
  if (pos < section.text.length) {
    segments.push({
      key: `t-${section.entry_id}-end`,
      text: section.text.slice(pos),
      marker: null,
    });
  }
  return segments;
}

function groupBySection(sections: GeneratedSection[]): Map<string, GeneratedSection[]> {
  const map = new Map<string, GeneratedSection[]>();
  for (const s of sections) {
    const bucket = map.get(s.section) ?? [];
    bucket.push(s);
    map.set(s.section, bucket);
  }
  return map;
}

/**
 * Overall in-scope coverage % — the same computation CoverageWidget has always
 * shown, lifted out so the headline metric tile shows an identical number.
 * (The backend's overall_pct is a Pydantic @property and is not serialised.)
 */
function overallCoveragePct(report: CoverageReport): number {
  let covered = 0;
  let total = 0;
  for (const s of report.sections) {
    covered += s.covered;
    total += s.total;
  }
  return total > 0 ? Math.round((covered / total) * 100) : 0;
}

/**
 * Render a fact value for humans without lying about types. Paise-shaped
 * integer keys use the display-layer formatter; everything else falls back
 * to a safe stringification.
 */
function renderFactValue(fact: Fact): string {
  const v = fact.value;
  if (typeof v === "number" && fact.key.endsWith("_paise") && Number.isInteger(v)) {
    return formatPaise(v);
  }
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

// --------------------------------------------------------------------------
// Sub-components
// --------------------------------------------------------------------------

function StatusPill({ state, label }: { state: LoadState; label: string }) {
  if (state === "loading") {
    return <span className="text-xs text-gray-500 italic">Loading {label}…</span>;
  }
  if (state === "error") {
    return <span className="text-xs text-red-600">Failed to load {label}.</span>;
  }
  return null;
}

function FactPanel({
  fact,
  factId,
  onClose,
}: {
  fact: Fact | undefined;
  factId: string;
  onClose: () => void;
}) {
  return (
    <aside
      className="fixed top-0 right-0 h-full w-full sm:w-96 bg-white border-l border-gray-200 shadow-xl z-40 flex flex-col"
      aria-label="Cited fact details"
    >
      <header className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
        <h3 className="text-sm font-semibold text-gray-900">Cited fact</h3>
        <button
          type="button"
          className="text-gray-500 hover:text-gray-900 text-sm"
          onClick={onClose}
        >
          Close
        </button>
      </header>
      <div className="p-4 space-y-3 overflow-y-auto text-sm">
        {fact ? (
          <>
            <div>
              <div className="text-xs uppercase tracking-wide text-gray-500">Key</div>
              <div className="font-mono text-gray-900 break-all">{fact.key}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-gray-500">Value</div>
              <div className="text-gray-900 break-words">{renderFactValue(fact)}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-gray-500">Source</div>
              <div className="text-gray-900">
                <span className="inline-block px-2 py-0.5 rounded bg-gray-100 text-gray-700 text-xs mr-2">
                  {fact.provenance.kind}
                </span>
                <span className="text-gray-700">{fact.provenance.detail}</span>
              </div>
            </div>
            {fact.provenance.snippet ? (
              <div>
                <div className="text-xs uppercase tracking-wide text-gray-500">Snippet</div>
                <blockquote className="border-l-4 border-gray-300 pl-3 text-gray-700 italic">
                  {fact.provenance.snippet}
                </blockquote>
              </div>
            ) : null}
            <div className="flex items-center gap-2 pt-1">
              <span className="text-xs text-gray-500">
                Confidence: {(fact.confidence * 100).toFixed(0)}%
              </span>
              {fact.confirmed ? (
                <span className="inline-block px-2 py-0.5 rounded bg-green-100 text-green-800 text-xs">
                  Confirmed
                </span>
              ) : (
                <span className="inline-block px-2 py-0.5 rounded bg-amber-100 text-amber-800 text-xs">
                  Unconfirmed
                </span>
              )}
            </div>
            <div className="text-xs text-gray-500 pt-2">
              Supplied by <span className="font-medium">{fact.supplied_by}</span>
              {" · "}
              <span className="font-mono">{fact.fact_id}</span>
            </div>
          </>
        ) : (
          <div className="text-gray-600">
            Fact <span className="font-mono">{factId}</span> was not found in the current fact
            store. It may have been superseded.
          </div>
        )}
      </div>
    </aside>
  );
}

function SectionBlock({
  entry,
  factsById,
  onCiteClick,
}: {
  entry: GeneratedSection;
  factsById: Map<string, Fact>;
  onCiteClick: (factId: string) => void;
}) {
  const segments = useMemo(() => buildSegments(entry), [entry]);
  const citedFacts = useMemo(() => {
    const ids = Array.from(new Set(entry.citations.map((c) => c.fact_id)));
    return ids.map((id) => ({ id, fact: factsById.get(id) }));
  }, [entry.citations, factsById]);

  return (
    <article
      id={`gen-${entry.entry_id}`}
      className="border border-gray-200 rounded-md bg-white p-4 scroll-mt-6"
    >
      <header className="mb-2 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-gray-900">{entry.entry_id}</h3>
          {entry.missing_facts.length > 0 ? (
            <p className="text-xs text-amber-700 mt-0.5">
              Missing facts: {entry.missing_facts.join(", ")}
            </p>
          ) : null}
        </div>
      </header>
      <p className="text-sm text-gray-800 whitespace-pre-wrap leading-6">
        {segments.map((seg) => {
          if (!seg.marker) {
            return <span key={seg.key}>{seg.text}</span>;
          }
          if (seg.marker.kind === "requires") {
            return (
              <span
                key={seg.key}
                className="inline-block mx-0.5 px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 border border-amber-300 text-xs font-medium align-middle"
                title="This value needs to be supplied before the draft is complete."
              >
                {seg.marker.label}
              </span>
            );
          }
          const cite = seg.marker;
          return (
            <span key={seg.key} className="relative">
              {seg.text}
              <button
                type="button"
                className="ml-0.5 align-super text-[0.65rem] text-blue-700 hover:text-blue-900 underline"
                onClick={() => onCiteClick(cite.factId)}
                aria-label={`Open source fact ${cite.factId}`}
              >
                [{cite.ordinal}]
              </button>
            </span>
          );
        })}
      </p>
      {citedFacts.length > 0 ? (
        <div className="mt-3 border-t border-gray-100 pt-2">
          <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">Sources</div>
          <ol className="text-xs text-gray-700 space-y-0.5 list-decimal list-inside">
            {citedFacts.map(({ id, fact }, i) => (
              <li key={id}>
                <button
                  type="button"
                  className="text-blue-700 hover:text-blue-900 underline"
                  onClick={() => onCiteClick(id)}
                >
                  [{i + 1}]
                </button>{" "}
                {fact ? (
                  <span>
                    <span className="font-mono">{fact.key}</span> — {renderFactValue(fact)}
                    <span className="text-gray-500">
                      {" "}
                      ({fact.provenance.kind}: {fact.provenance.detail})
                    </span>
                  </span>
                ) : (
                  <span className="text-gray-500">fact {id} (not found)</span>
                )}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </article>
  );
}

function ContradictionsList({ items }: { items: Contradiction[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-green-700">No contradictions found.</p>;
  }
  return (
    <ul className="space-y-3">
      {items.map((c, i) => (
        <li key={`${c.subject}-${i}`} className="border border-red-200 rounded bg-red-50 p-3">
          <div className="text-sm font-semibold text-red-900 mb-2">
            Disagreement on: <span className="font-mono">{c.subject}</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {c.claims.map((claim, j) => (
              <div key={j} className="bg-white border border-red-200 rounded p-2 text-xs">
                <div className="text-gray-500">Section</div>
                <div className="font-mono text-gray-900 mb-1">{claim.section_entry_id}</div>
                <div className="text-gray-500">Claimed value</div>
                <div className="text-gray-900 break-words">{claim.value}</div>
                <div className="text-gray-500 mt-1">Kind</div>
                <div className="text-gray-700">{claim.kind}</div>
              </div>
            ))}
          </div>
        </li>
      ))}
    </ul>
  );
}

function BoilerplateList({ items }: { items: BoilerplateFlag[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-green-700">No boilerplate flagged.</p>;
  }
  return (
    <ul className="space-y-2">
      {items.map((b, i) => (
        <li
          key={`${b.entry_id}-${i}`}
          className="border border-amber-200 rounded bg-amber-50 p-3 text-sm"
        >
          <div className="font-mono text-gray-900">{b.entry_id}</div>
          <div className="text-amber-900">{b.reason}</div>
          <div className="text-xs text-gray-500">
            span [{b.text_span[0]}, {b.text_span[1]}]
          </div>
        </li>
      ))}
    </ul>
  );
}

function ObjectionsList({ items }: { items: Objection[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-green-700">Examiner has no objections.</p>;
  }
  return (
    <ul className="space-y-2">
      {items.map((o, i) => (
        <li
          key={`${o.entry_id}-${i}`}
          className="border border-gray-200 rounded bg-white p-3 text-sm"
        >
          <div className="flex items-center gap-2 mb-1">
            <span className="font-mono text-gray-900">{o.entry_id}</span>
            {o.clause_ref ? (
              <span className="inline-block px-2 py-0.5 rounded bg-blue-100 text-blue-800 text-xs">
                {o.clause_ref}
              </span>
            ) : null}
            {o.resolved ? (
              <span className="inline-block px-2 py-0.5 rounded bg-green-100 text-green-800 text-xs">
                Resolved
              </span>
            ) : null}
          </div>
          <div className="text-gray-800">{o.objection}</div>
        </li>
      ))}
    </ul>
  );
}

function SeverityBadge({ severity }: { severity: Severity }) {
  const cls =
    severity === "blocker"
      ? "bg-red-100 text-red-800"
      : severity === "material"
        ? "bg-amber-100 text-amber-800"
        : "bg-gray-100 text-gray-700";
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${cls}`}>
      {severity}
    </span>
  );
}

type TileTone = "neutral" | "green" | "amber" | "red";

const TILE_TONE: Record<TileTone, { box: string; value: string; hint: string }> = {
  neutral: { box: "border-gray-200 bg-white", value: "text-gray-900", hint: "text-gray-500" },
  green: { box: "border-green-300 bg-green-50", value: "text-green-700", hint: "text-green-700" },
  amber: { box: "border-amber-300 bg-amber-50", value: "text-amber-800", hint: "text-amber-800" },
  red: { box: "border-red-300 bg-red-50", value: "text-red-700", hint: "text-red-700 font-medium" },
};

/** One headline stat. The whole tile is a button that scrolls to its detail. */
function MetricTile({
  label,
  value,
  tone,
  hint,
  onClick,
}: {
  label: string;
  value: string;
  tone: TileTone;
  hint?: string;
  onClick: () => void;
}) {
  const t = TILE_TONE[tone];
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md border p-3 text-left hover:shadow transition-shadow ${t.box}`}
    >
      <div className="text-[0.65rem] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`text-2xl font-semibold leading-tight ${t.value}`}>{value}</div>
      {hint ? <div className={`text-xs mt-0.5 ${t.hint}`}>{hint}</div> : null}
    </button>
  );
}

/**
 * Prominent banner shown above the draft whenever the contradiction check has
 * findings — the planted-error demo moment, surfaced without any clicking.
 */
function ContradictionBanner({ items }: { items: Contradiction[] }) {
  return (
    <div
      id="contradiction-banner"
      role="alert"
      className="mb-6 border border-red-300 border-l-4 border-l-red-600 rounded bg-red-50 p-4 scroll-mt-6"
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className="mt-0.5 inline-flex h-5 w-5 flex-none items-center justify-center rounded-full bg-red-600 text-white text-xs font-bold"
        >
          !
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-red-900">
            {items.length} contradiction{items.length === 1 ? "" : "s"} between confirmed sources
            — resolve before certification
          </p>
          <div className="mt-2 space-y-3">
            {items.map((c, i) => (
              <div key={`${c.subject}-${i}`}>
                <div className="text-xs font-mono text-red-800 mb-1">{c.subject}</div>
                <div className="flex flex-wrap items-center gap-2">
                  {c.claims.map((claim, j) => (
                    <div key={j} className="flex items-center gap-2">
                      {j > 0 ? (
                        <span className="text-xs font-semibold uppercase text-red-500">vs</span>
                      ) : null}
                      <div className="bg-white border border-red-300 rounded px-2 py-1">
                        <div className="text-sm font-semibold text-red-900 break-words">
                          {claim.value}
                        </div>
                        <div className="text-[0.65rem] font-mono text-gray-500">
                          {claim.section_entry_id}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function ArithmeticList({ items }: { items: ArithmeticFinding[] }) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-green-700">
        Objects of the Issue arithmetic checks out: allocations + GCP reconcile with the issue
        size.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {items.map((f, i) => (
        <li key={`${f.kind}-${i}`} className="border border-gray-200 rounded bg-white p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <SeverityBadge severity={f.severity} />
            <span className="font-mono text-xs text-gray-500">{f.kind}</span>
            {f.clause_ref ? (
              <span className="inline-block px-2 py-0.5 rounded bg-blue-100 text-blue-800 text-xs">
                {f.clause_ref}
              </span>
            ) : null}
          </div>
          <div className="text-gray-800">{f.detail}</div>
        </li>
      ))}
    </ul>
  );
}

// Role-aware routing language, matching the gap report's framing.
const ROUTED_LABEL: Record<Role, string> = {
  promoter: "promoter-fixable",
  auditor: "needs your auditor",
  banker: "needs your merchant banker",
  system: "system",
};

function GapsList({ report }: { report: GapReport }) {
  if (report.gaps.length === 0) {
    return (
      <p className="text-sm text-green-700">
        No gaps — every applicable requirement has the facts it needs.
      </p>
    );
  }
  return (
    <ul className="space-y-2 max-h-80 overflow-y-auto pr-1">
      {report.gaps.map((g, i) => (
        <li
          key={`${g.entry_id}-${g.missing_fact_key}-${i}`}
          className="border border-gray-200 rounded bg-white p-2.5 text-xs"
        >
          <div className="font-mono text-gray-900 break-all">{g.missing_fact_key}</div>
          <div className="text-gray-600 mt-0.5">
            {g.section} · <span className="font-mono">{g.entry_id}</span>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <SeverityBadge severity={g.severity} />
            <span className="inline-block px-1.5 py-0.5 rounded bg-blue-50 text-blue-800 border border-blue-200">
              {ROUTED_LABEL[g.routed_to]}
            </span>
            <span className="inline-block px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
              {g.clause_ref}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}

/**
 * In-scope match stats for one reference filing, computed client-side with the
 * same semantics as the backend summary: a chapter counts as encoded when it
 * maps to at least one checklist entry; auditor-only chapters are out of scope.
 */
function filingStats(ref: ReferenceBenchmark): {
  total: number;
  encoded: number;
  outOfScope: number;
  pct: number;
} {
  const total = ref.chapters.length;
  const encoded = ref.chapters.filter((c) => c.maps_to.length > 0).length;
  const outOfScope = ref.chapters.filter(
    (c) => c.maps_to.length === 0 && c.status === "out_of_scope_auditor",
  ).length;
  const inScope = total - outOfScope;
  const pct = inScope > 0 ? Math.round((encoded / inScope) * 100) : 0;
  return { total, encoded, outOfScope, pct };
}

/**
 * Side-by-side comparison against real filed SME DRHPs: left column is the
 * filing's own chapter list, right column is how the checklist schema covers
 * each chapter. Evidence, not a claim.
 */
function BenchmarkPanel({
  report,
  generatedIds,
  onEntryClick,
}: {
  report: BenchmarkReport;
  generatedIds: Set<string>;
  onEntryClick: (entryId: string) => void;
}) {
  const [active, setActive] = useState(0);
  const refs = report.references;
  const activeIdx = refs.length > 0 ? Math.min(active, refs.length - 1) : -1;
  const current = activeIdx >= 0 ? refs[activeIdx] : undefined;

  if (!current) {
    return <p className="text-sm text-gray-600">No reference filings bundled.</p>;
  }
  const stats = filingStats(current);

  return (
    <div>
      <div className="flex flex-wrap gap-1 border-b border-gray-200 mb-3" role="tablist">
        {refs.map((r, i) => (
          <button
            key={r.company}
            type="button"
            role="tab"
            aria-selected={i === activeIdx}
            onClick={() => setActive(i)}
            className={`px-3 py-1.5 text-xs rounded-t border ${
              i === activeIdx
                ? "bg-white border-gray-300 border-b-white font-semibold text-gray-900 -mb-px"
                : "bg-gray-50 border-transparent text-gray-600 hover:text-gray-900"
            }`}
          >
            {r.company}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-1">
        <span className="text-sm font-semibold text-gray-900">{current.company}</span>
        <span className="inline-block px-2 py-0.5 rounded bg-blue-100 text-blue-800 text-xs">
          {current.exchange}
        </span>
        <span className="text-xs text-gray-500">filed {current.filed}</span>
        <span className="text-xs font-medium text-gray-900">
          in-scope match: {stats.pct}% ({stats.encoded}/{stats.total - stats.outOfScope} chapters)
        </span>
        <a
          href={current.source_url}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-blue-700 hover:text-blue-900 underline"
        >
          source filing
        </a>
      </div>
      <p className="text-xs text-gray-500 italic mb-3">{current.framework_evidence}</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 pb-1 border-b border-gray-200 text-[0.65rem] uppercase tracking-wide text-gray-500">
        <div>Chapter in the filed DRHP</div>
        <div className="hidden sm:block">Covered by the checklist schema</div>
      </div>
      <div className="divide-y divide-gray-100">
        {current.chapters.map((ch, i) => (
          <div
            key={`${ch.heading}-${i}`}
            className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 py-2"
          >
            <div className="text-sm text-gray-800">{ch.heading}</div>
            <div className="flex flex-wrap items-center gap-1.5">
              {ch.maps_to.map((id) =>
                generatedIds.has(id) ? (
                  <button
                    key={id}
                    type="button"
                    onClick={() => onEntryClick(id)}
                    className="inline-block px-1.5 py-0.5 rounded bg-green-100 text-green-800 border border-green-300 text-xs font-mono hover:bg-green-200"
                    title="Jump to this generated section"
                  >
                    {id}
                  </button>
                ) : (
                  <span
                    key={id}
                    className="inline-block px-1.5 py-0.5 rounded bg-green-50 text-green-700 border border-green-200 text-xs font-mono"
                    title="Encoded in the checklist schema (section not generated yet)"
                  >
                    {id}
                  </span>
                ),
              )}
              {ch.status === "out_of_scope_auditor" ? (
                <span className="inline-block px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 border border-gray-200 text-xs">
                  auditor content — out of scope by design
                </span>
              ) : null}
              {ch.maps_to.length === 0 && ch.status === "not_encoded" ? (
                <span className="inline-block px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 border border-amber-300 text-xs">
                  not yet encoded
                </span>
              ) : null}
              {ch.note ? <span className="w-full text-xs text-gray-400 italic">{ch.note}</span> : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CoverageWidget({ report }: { report: CoverageReport }) {
  const overall = useMemo(() => overallCoveragePct(report), [report]);

  return (
    <div className="border border-gray-200 rounded bg-white p-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-900">Coverage</h3>
        <span className="text-sm text-gray-700">Overall: {overall}%</span>
      </div>
      <ul className="space-y-2">
        {report.sections.map((s) => {
          const pct = s.total > 0 ? Math.round((s.covered / s.total) * 100) : 0;
          return (
            <li key={s.section}>
              <div className="flex items-center justify-between text-xs text-gray-700 mb-0.5">
                <span>{s.section}</span>
                <span>
                  {s.covered}/{s.total}
                  {s.out_of_scope > 0 ? ` (+${s.out_of_scope} out of scope)` : ""}
                </span>
              </div>
              <div className="h-2 bg-gray-100 rounded overflow-hidden">
                <div
                  className="h-2 bg-blue-500"
                  style={{ width: `${pct}%` }}
                  aria-label={`${pct}% covered`}
                />
              </div>
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-gray-500 mt-3">
        Auditor-supplied content is out of scope and not counted.
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------
// Main page
// --------------------------------------------------------------------------

export default function DraftViewer() {
  const [sections, setSections] = useState<GeneratedSection[]>([]);
  const [sectionsState, setSectionsState] = useState<LoadState>("idle");
  const [sectionsError, setSectionsError] = useState<string | null>(null);

  const [generateState, setGenerateState] = useState<LoadState>("idle");
  const [generateError, setGenerateError] = useState<string | null>(null);

  const [facts, setFacts] = useState<Fact[]>([]);
  const [factsState, setFactsState] = useState<LoadState>("idle");

  const [openFactId, setOpenFactId] = useState<string | null>(null);

  const [validationOpen, setValidationOpen] = useState(false);

  const [contradictions, setContradictions] = useState<Contradiction[] | null>(null);
  const [contradictionsState, setContradictionsState] = useState<LoadState>("idle");
  const [boilerplate, setBoilerplate] = useState<BoilerplateFlag[] | null>(null);
  const [boilerplateState, setBoilerplateState] = useState<LoadState>("idle");
  const [objections, setObjections] = useState<Objection[] | null>(null);
  const [examinerState, setExaminerState] = useState<LoadState>("idle");

  const [coverage, setCoverage] = useState<CoverageReport | null>(null);
  const [coverageState, setCoverageState] = useState<LoadState>("idle");

  const [arithmetic, setArithmetic] = useState<ArithmeticFinding[] | null>(null);
  const [arithmeticState, setArithmeticState] = useState<LoadState>("idle");

  const [gapReport, setGapReport] = useState<GapReport | null>(null);
  const [gapsState, setGapsState] = useState<LoadState>("idle");
  const [gapsOpen, setGapsOpen] = useState(false);

  const [benchmark, setBenchmark] = useState<BenchmarkReport | null>(null);
  const [benchmarkState, setBenchmarkState] = useState<LoadState>("idle");
  const [benchmarkOpen, setBenchmarkOpen] = useState(false);

  // Scroll-to-detail plumbing for the metric tiles and benchmark chips. Click
  // handlers set a DOM id here; the effect below scrolls after the next render
  // (which matters when the same click also expands a collapsed panel).
  const [scrollTarget, setScrollTarget] = useState<string | null>(null);

  const factsById = useMemo(() => {
    const m = new Map<string, Fact>();
    for (const f of facts) m.set(f.fact_id, f);
    return m;
  }, [facts]);

  const loadSections = useCallback(async () => {
    setSectionsState("loading");
    setSectionsError(null);
    try {
      const data = await getSections();
      setSections(data);
      setSectionsState("ready");
    } catch (e) {
      setSectionsError(e instanceof Error ? e.message : "Unknown error");
      setSectionsState("error");
    }
  }, []);

  const loadFacts = useCallback(async () => {
    setFactsState("loading");
    try {
      const data = await getFacts();
      setFacts(data);
      setFactsState("ready");
    } catch {
      setFactsState("error");
    }
  }, []);

  const loadCoverage = useCallback(async () => {
    setCoverageState("loading");
    try {
      const data = await getCoverage();
      setCoverage(data);
      setCoverageState("ready");
    } catch {
      setCoverageState("error");
    }
  }, []);

  useEffect(() => {
    void loadSections();
    void loadFacts();
    void loadCoverage();
  }, [loadSections, loadFacts, loadCoverage]);

  const onGenerate = async () => {
    setGenerateState("loading");
    setGenerateError(null);
    try {
      const data = await postGenerate();
      setSections(data);
      setSectionsState("ready");
      setGenerateState("ready");
      // Refresh facts and coverage so citations resolve against the latest store.
      void loadFacts();
      void loadCoverage();
    } catch (e) {
      setGenerateError(e instanceof Error ? e.message : "Unknown error");
      setGenerateState("error");
    }
  };

  // useCallback (unlike the other run* handlers) because the evidence-strip
  // effect below re-runs it whenever a new draft lands.
  const runContradictions = useCallback(async () => {
    setContradictionsState("loading");
    try {
      const data = await getContradictions();
      setContradictions(data);
      setContradictionsState("ready");
    } catch {
      setContradictionsState("error");
    }
  }, []);

  const runBoilerplate = async () => {
    setBoilerplateState("loading");
    try {
      const data = await getBoilerplate();
      setBoilerplate(data);
      setBoilerplateState("ready");
    } catch {
      setBoilerplateState("error");
    }
  };

  const runExaminer = async () => {
    setExaminerState("loading");
    try {
      const data = await getExaminer();
      setObjections(data);
      setExaminerState("ready");
    } catch {
      setExaminerState("error");
    }
  };

  const runArithmetic = useCallback(async () => {
    setArithmeticState("loading");
    try {
      const data = await getArithmetic();
      setArithmetic(data);
      setArithmeticState("ready");
    } catch {
      setArithmeticState("error");
    }
  }, []);

  const loadGaps = useCallback(async () => {
    setGapsState("loading");
    try {
      const data = await getGaps();
      setGapReport(data);
      setGapsState("ready");
    } catch {
      setGapsState("error");
    }
  }, []);

  const loadBenchmark = useCallback(async () => {
    setBenchmarkState("loading");
    try {
      const data = await getBenchmark();
      setBenchmark(data);
      setBenchmarkState("ready");
    } catch {
      setBenchmarkState("error");
    }
  }, []);

  // Judge-facing evidence: once a draft exists (loaded or freshly generated),
  // pull gap / contradiction / arithmetic results automatically so the
  // headline strip and the contradiction banner are live without any clicks.
  // All three are deterministic GET checks — no LLM call is triggered here.
  useEffect(() => {
    if (sections.length === 0) return;
    void loadGaps();
    void runContradictions();
    void runArithmetic();
  }, [sections, loadGaps, runContradictions, runArithmetic]);

  // Smooth-scroll to a detail target after the render that (possibly) opened it.
  useEffect(() => {
    if (!scrollTarget) return;
    const el = document.getElementById(scrollTarget);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    setScrollTarget(null);
  }, [scrollTarget]);

  const jumpTo = (id: string): void => setScrollTarget(id);

  const openValidationAt = (id: string): void => {
    setValidationOpen(true);
    setScrollTarget(id);
  };

  const toggleGaps = (): void => {
    const next = !gapsOpen;
    setGapsOpen(next);
    if (next && gapReport === null && gapsState !== "loading") void loadGaps();
  };

  const openGapsCard = (): void => {
    if (gapReport === null && gapsState !== "loading") void loadGaps();
    setGapsOpen(true);
    setScrollTarget("panel-gaps");
  };

  const toggleBenchmark = (): void => {
    const next = !benchmarkOpen;
    setBenchmarkOpen(next);
    if (next && benchmark === null && benchmarkState !== "loading") void loadBenchmark();
  };

  const onContradictionTile = (): void => {
    if (contradictions && contradictions.length > 0) {
      setScrollTarget("contradiction-banner");
    } else {
      openValidationAt("panel-contradictions");
    }
  };

  const grouped = useMemo(() => groupBySection(sections), [sections]);
  const openFact = openFactId ? factsById.get(openFactId) : undefined;

  const generatedIds = useMemo(() => new Set(sections.map((s) => s.entry_id)), [sections]);
  const coveragePct = coverage ? overallCoveragePct(coverage) : null;
  const gapCount = gapReport ? gapReport.gaps.length : null;
  const contradictionCount = contradictions ? contradictions.length : null;
  const arithmeticCount = arithmetic ? arithmetic.length : null;

  // "…" while a count is still loading, "—" if its endpoint failed.
  const tileValue = (count: number | null, state: LoadState): string =>
    count !== null ? String(count) : state === "error" ? "—" : "…";

  return (
    <section className="pb-16">
      <header className="mb-4">
        <h1 className="text-2xl font-semibold mb-1">Draft DRHP</h1>
        <p className="text-gray-600 text-sm">
          Your draft, section by section. Every claim links back to the fact that supports it.
          Amber chips mark information the draft still needs before it is complete.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3 mb-6">
        <button
          type="button"
          className="px-4 py-2 rounded bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:bg-blue-300"
          onClick={onGenerate}
          disabled={generateState === "loading"}
        >
          {generateState === "loading" ? "Generating…" : "Generate draft"}
        </button>
        <a
          href={assembleUrl("drhp")}
          download
          className="px-3 py-2 rounded border border-gray-300 text-sm text-gray-800 hover:bg-gray-50"
        >
          Download DRHP (.docx)
        </a>
        <a
          href={assembleUrl("abridged")}
          download
          className="px-3 py-2 rounded border border-gray-300 text-sm text-gray-800 hover:bg-gray-50"
        >
          Download draft abridged prospectus (.docx)
        </a>
        <StatusPill state={sectionsState} label="draft sections" />
        <StatusPill state={factsState} label="facts" />
        {generateState === "error" && generateError ? (
          <span className="text-xs text-red-600">Generation failed: {generateError}</span>
        ) : null}
        {sectionsError ? (
          <span className="text-xs text-red-600">Sections error: {sectionsError}</span>
        ) : null}
      </div>

      {sections.length > 0 ? (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
          <MetricTile
            label="Sections generated"
            value={tileValue(sections.length, sectionsState)}
            tone="neutral"
            onClick={() => jumpTo("draft-sections")}
          />
          <MetricTile
            label="Coverage"
            value={coveragePct !== null ? `${coveragePct.toFixed(0)}%` : tileValue(null, coverageState)}
            hint="of in-scope sections"
            tone={coveragePct !== null && coveragePct >= 70 ? "green" : "amber"}
            onClick={() => jumpTo("panel-coverage")}
          />
          <MetricTile
            label="Gaps"
            value={tileValue(gapCount, gapsState)}
            tone={gapCount === 0 ? "green" : "amber"}
            onClick={openGapsCard}
          />
          <MetricTile
            label="Contradictions"
            value={tileValue(contradictionCount, contradictionsState)}
            tone={contradictionCount === null ? "neutral" : contradictionCount === 0 ? "green" : "red"}
            onClick={onContradictionTile}
          />
          <MetricTile
            label="Arithmetic findings"
            value={tileValue(arithmeticCount, arithmeticState)}
            hint="objects vs. issue size"
            tone={arithmeticCount === null ? "neutral" : arithmeticCount === 0 ? "green" : "amber"}
            onClick={() => openValidationAt("panel-arithmetic")}
          />
        </div>
      ) : null}

      {contradictions && contradictions.length > 0 ? (
        <div id="contradiction-banner" className="mb-6">
          <ContradictionBanner items={contradictions} />
        </div>
      ) : null}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div id="draft-sections" className="lg:col-span-2 space-y-6">
          {sectionsState === "ready" && sections.length === 0 ? (
            <div className="border border-dashed border-gray-300 rounded p-6 text-sm text-gray-600 bg-white">
              No draft yet. Click <span className="font-semibold">Generate draft</span> to build
              one from the confirmed facts in the store.
            </div>
          ) : null}

          {Array.from(grouped.entries()).map(([sectionName, entries]) => (
            <div key={sectionName} className="space-y-3">
              <h2 className="text-lg font-semibold text-gray-900 border-b border-gray-200 pb-1">
                {sectionName}
              </h2>
              {entries.map((entry) => (
                <SectionBlock
                  key={entry.entry_id}
                  entry={entry}
                  factsById={factsById}
                  onCiteClick={setOpenFactId}
                />
              ))}
            </div>
          ))}
        </div>

        <aside className="space-y-6">
          <div id="panel-coverage">
            {coverageState === "ready" && coverage ? (
              <CoverageWidget report={coverage} />
            ) : (
              <div className="border border-gray-200 rounded bg-white p-4 text-sm text-gray-600">
                <div className="flex items-center justify-between mb-1">
                  <h3 className="text-sm font-semibold text-gray-900">Coverage</h3>
                  <StatusPill state={coverageState} label="coverage" />
                </div>
                <p className="text-xs text-gray-500">
                  Auditor-supplied content is out of scope and not counted.
                </p>
              </div>
            )}
          </div>

          <div id="panel-gaps" className="border border-gray-200 rounded bg-white">
            <button
              type="button"
              className="w-full flex items-center justify-between px-4 py-3 text-left"
              onClick={toggleGaps}
              aria-expanded={gapsOpen}
            >
              <span className="text-sm font-semibold text-gray-900">
                Gaps {gapCount !== null ? <span className="text-gray-500 font-normal">({gapCount})</span> : null}
              </span>
              <span className="text-xs text-gray-500">{gapsOpen ? "Hide" : "Show"}</span>
            </button>
            {gapsOpen ? (
              <div className="px-4 pb-4">
                {gapReport ? <GapsList report={gapReport} /> : (
                  <p className="text-xs text-gray-500">
                    <StatusPill state={gapsState} label="gaps" />
                  </p>
                )}
              </div>
            ) : null}
          </div>

          <div id="panel-benchmark" className="border border-gray-200 rounded bg-white">
            <button
              type="button"
              className="w-full flex items-center justify-between px-4 py-3 text-left"
              onClick={toggleBenchmark}
              aria-expanded={benchmarkOpen}
            >
              <span className="text-sm font-semibold text-gray-900">Benchmark vs filed DRHPs</span>
              <span className="text-xs text-gray-500">{benchmarkOpen ? "Hide" : "Show"}</span>
            </button>
            {benchmarkOpen ? (
              <div className="px-4 pb-4">
                {benchmark ? (
                  <BenchmarkPanel
                    report={benchmark}
                    generatedIds={generatedIds}
                    onEntryClick={(entryId: string) => jumpTo(`section-${entryId}`)}
                  />
                ) : (
                  <p className="text-xs text-gray-500">
                    <StatusPill state={benchmarkState} label="benchmark" />
                  </p>
                )}
              </div>
            ) : null}
          </div>

          <div className="border border-gray-200 rounded bg-white">
            <button
              type="button"
              className="w-full flex items-center justify-between px-4 py-3 text-left"
              onClick={() => setValidationOpen((v) => !v)}
              aria-expanded={validationOpen}
            >
              <span className="text-sm font-semibold text-gray-900">Validation checks</span>
              <span className="text-xs text-gray-500">{validationOpen ? "Hide" : "Show"}</span>
            </button>
            {validationOpen ? (
              <div className="px-4 pb-4 space-y-4">
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <button
                      type="button"
                      className="px-3 py-1.5 rounded bg-gray-900 text-white text-xs hover:bg-gray-700"
                      onClick={runContradictions}
                      disabled={contradictionsState === "loading"}
                    >
                      {contradictionsState === "loading"
                        ? "Checking…"
                        : "Run contradiction check"}
                    </button>
                    <StatusPill state={contradictionsState} label="contradictions" />
                  </div>
                  {contradictions !== null ? (
                    <ContradictionsList items={contradictions} />
                  ) : null}
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <button
                      type="button"
                      className="px-3 py-1.5 rounded bg-gray-900 text-white text-xs hover:bg-gray-700"
                      onClick={runBoilerplate}
                      disabled={boilerplateState === "loading"}
                    >
                      {boilerplateState === "loading" ? "Checking…" : "Run boilerplate check"}
                    </button>
                    <StatusPill state={boilerplateState} label="boilerplate" />
                  </div>
                  {boilerplate !== null ? <BoilerplateList items={boilerplate} /> : null}
                </div>

                <div id="panel-arithmetic">
                  <div className="flex items-center gap-2 mb-2">
                    <button
                      type="button"
                      className="px-3 py-1.5 rounded bg-gray-900 text-white text-xs hover:bg-gray-700"
                      onClick={runArithmetic}
                      disabled={arithmeticState === "loading"}
                    >
                      {arithmeticState === "loading" ? "Checking…" : "Objects arithmetic"}
                    </button>
                    <StatusPill state={arithmeticState} label="arithmetic" />
                  </div>
                  {arithmetic !== null ? (
                    arithmetic.length > 0 ? (
                      <ArithmeticList items={arithmetic} />
                    ) : (
                      <p className="text-xs text-gray-600">
                        Objects of the Issue arithmetic checks out: allocations and GCP reconcile
                        with the issue size.
                      </p>
                    )
                  ) : null}
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <button
                      type="button"
                      className="px-3 py-1.5 rounded bg-gray-900 text-white text-xs hover:bg-gray-700"
                      onClick={runExaminer}
                      disabled={examinerState === "loading"}
                    >
                      {examinerState === "loading" ? "Running…" : "Run examiner"}
                    </button>
                    <StatusPill state={examinerState} label="examiner" />
                  </div>
                  {objections !== null ? <ObjectionsList items={objections} /> : null}
                </div>
              </div>
            ) : null}
          </div>
        </aside>
      </div>

      {openFactId ? (
        <FactPanel fact={openFact} factId={openFactId} onClose={() => setOpenFactId(null)} />
      ) : null}
    </section>
  );
}
