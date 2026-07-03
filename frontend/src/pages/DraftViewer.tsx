import { useCallback, useEffect, useMemo, useState } from "react";
import {
  assembleUrl,
  formatPaise,
  getBoilerplate,
  getContradictions,
  getCoverage,
  getExaminer,
  getFacts,
  getSections,
  postGenerate,
  type BoilerplateFlag,
  type Citation,
  type Contradiction,
  type CoverageReport,
  type Fact,
  type GeneratedSection,
  type Objection,
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
    <article className="border border-gray-200 rounded-md bg-white p-4">
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

function CoverageWidget({ report }: { report: CoverageReport }) {
  const overall = useMemo(() => {
    let covered = 0;
    let total = 0;
    for (const s of report.sections) {
      covered += s.covered;
      total += s.total;
    }
    return total > 0 ? Math.round((covered / total) * 100) : 0;
  }, [report]);

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

  const runContradictions = async () => {
    setContradictionsState("loading");
    try {
      const data = await getContradictions();
      setContradictions(data);
      setContradictionsState("ready");
    } catch {
      setContradictionsState("error");
    }
  };

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

  const grouped = useMemo(() => groupBySection(sections), [sections]);
  const openFact = openFactId ? factsById.get(openFactId) : undefined;

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

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
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
