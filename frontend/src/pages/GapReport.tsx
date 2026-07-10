import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getCoverage, getGaps, getSchema, getWizardQuestions } from "../api/client";
import type {
  ChecklistEntry,
  CoverageReport,
  Gap,
  GapReport as GapReportPayload,
  Role,
  Severity,
} from "../api/client";

// --------------------------------------------------------------------------
// Role + severity presentation helpers. Copy is promoter-first plain English;
// Hindi chrome is demo-grade UX translation (same scope note as the wizard's
// question_copy.yaml). Clause refs and severity terms stay in English — the
// regulation is authoritative in English.
// --------------------------------------------------------------------------

type Lang = "en" | "hi";

const ROLE_ORDER: Role[] = ["promoter", "auditor", "banker", "system"];

const UI: Record<Lang, {
  title: string;
  intro: string;
  refresh: string;
  refreshing: string;
  loading: string;
  loadErrorTitle: string;
  retry: string;
  openGaps: (n: number) => string;
  gapExplainer: string;
  emptyTitle: string;
  emptySub: string;
  items: (n: number) => string;
  requirement: string;
  whatThisMeans: string;
  whoFixes: string;
  fixInWizard: string;
  coverageLabel: string;
  coverageNote: string;
  roleHeaders: Record<Role, string>;
  roleBlurbs: Record<Role, string>;
}> = {
  en: {
    title: "Gap Report",
    intro:
      "What is still missing from your draft, and who can provide it. Every " +
      "requirement here traces back to an ICDR Chapter IX clause — nothing is invented.",
    refresh: "Refresh",
    refreshing: "Refreshing…",
    loading: "Loading your gap report…",
    loadErrorTitle: "We could not load your gap report.",
    retry: "Try again",
    openGaps: (n) => (n === 1 ? "open gap" : "open gaps"),
    gapExplainer: "Each gap is a required disclosure whose fact is missing or unconfirmed.",
    emptyTitle: "No gaps — every requirement has a confirmed fact.",
    emptySub: "Your draft is ready for validation and merchant-banker review.",
    items: (n) => (n === 1 ? "item" : "items"),
    requirement: "Requirement",
    whatThisMeans: "What this means",
    whoFixes: "Who provides this",
    fixInWizard: "Fix this in the Wizard →",
    coverageLabel: "Draft completeness",
    coverageNote: "Auditor-supplied content is out of scope and not counted.",
    roleHeaders: {
      promoter: "You can fix these",
      auditor: "Needs your auditor",
      banker: "Needs your merchant banker",
      system: "System items",
    },
    roleBlurbs: {
      promoter: "Answer these questions in the wizard or upload a document that has the answer.",
      auditor:
        "Your peer-reviewed auditor has to prepare and sign these — the tool ingests and formats them but never writes them.",
      banker: "Your merchant banker (Lead Manager) has to supply these as part of their due diligence.",
      system: "Housekeeping items handled by the tool.",
    },
  },
  hi: {
    title: "कमी रिपोर्ट",
    intro:
      "आपके ड्राफ़्ट में अभी क्या कमी है, और कौन उसे पूरा कर सकता है। " +
      "यहाँ हर आवश्यकता ICDR अध्याय IX की धारा से जुड़ी है — कुछ भी मनगढ़ंत नहीं है।",
    refresh: "रीफ़्रेश करें",
    refreshing: "रीफ़्रेश हो रहा है…",
    loading: "आपकी कमी रिपोर्ट लोड हो रही है…",
    loadErrorTitle: "हम आपकी कमी रिपोर्ट लोड नहीं कर सके।",
    retry: "फिर से कोशिश करें",
    openGaps: (n) => (n === 1 ? "खुली कमी" : "खुली कमियाँ"),
    gapExplainer: "हर कमी एक अनिवार्य प्रकटीकरण है जिसका तथ्य अभी नहीं मिला या पुष्ट नहीं हुआ।",
    emptyTitle: "कोई कमी नहीं — हर आवश्यकता का तथ्य पुष्ट है।",
    emptySub: "आपका ड्राफ़्ट सत्यापन और मर्चेंट बैंकर समीक्षा के लिए तैयार है।",
    items: (n) => (n === 1 ? "मद" : "मदें"),
    requirement: "आवश्यकता",
    whatThisMeans: "इसका क्या मतलब है",
    whoFixes: "यह कौन देगा",
    fixInWizard: "विज़ार्ड में इसे भरें →",
    coverageLabel: "ड्राफ़्ट पूर्णता",
    coverageNote: "ऑडिटर द्वारा दी जाने वाली सामग्री दायरे से बाहर है और गिनी नहीं जाती।",
    roleHeaders: {
      promoter: "आप इन्हें ठीक कर सकते हैं",
      auditor: "आपके ऑडिटर की ज़रूरत है",
      banker: "आपके मर्चेंट बैंकर की ज़रूरत है",
      system: "सिस्टम मदें",
    },
    roleBlurbs: {
      promoter: "विज़ार्ड में इन सवालों के जवाब दें या ऐसा दस्तावेज़ अपलोड करें जिसमें जवाब हो।",
      auditor:
        "ये आपके सहकर्मी-समीक्षित ऑडिटर को तैयार कर हस्ताक्षर करने हैं — टूल इन्हें लेता और स्वरूपित करता है, कभी लिखता नहीं।",
      banker: "ये आपके मर्चेंट बैंकर (लीड मैनेजर) को अपनी due diligence के तहत देने हैं।",
      system: "टूल द्वारा संभाली जाने वाली मदें।",
    },
  },
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
      className="inline-block max-w-full break-words whitespace-normal rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-700"
      title={clauseRef}
    >
      {clauseRef}
    </span>
  );
}

function GapRow({
  gap,
  t,
  entry,
  hindiLabel,
  expanded,
  onToggle,
}: {
  gap: Gap;
  t: (typeof UI)[Lang];
  entry: ChecklistEntry | undefined;
  hindiLabel: string | undefined;
  expanded: boolean;
  onToggle: () => void;
}) {
  const label = hindiLabel ?? humaniseFactKey(gap.missing_fact_key);
  return (
    <li className="border-t border-gray-100 py-3 first:border-t-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full flex-col gap-2 text-left sm:flex-row sm:items-start sm:justify-between sm:gap-4"
      >
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-gray-900">{gap.section}</div>
          <div className="mt-0.5 text-sm text-gray-700 break-words" title={gap.missing_fact_key}>
            {label}
          </div>
          <div className="mt-1 text-xs text-gray-500 break-words" title={gap.entry_id}>
            {t.requirement}: <span className="font-mono">{gap.entry_id}</span>
          </div>
        </div>
        <div className="flex w-full shrink-0 flex-wrap items-start gap-2 sm:w-64 sm:justify-end md:w-72">
          <ClauseChip clauseRef={gap.clause_ref} />
          <SeverityBadge severity={gap.severity} />
        </div>
      </button>
      {expanded ? (
        <div className="mt-2 rounded border border-blue-100 bg-blue-50/50 p-3 text-sm">
          {entry ? (
            <>
              <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                {t.whatThisMeans}
              </div>
              <p className="mt-1 whitespace-pre-line text-gray-800">{entry.description.trim()}</p>
            </>
          ) : null}
          <div className="mt-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            {t.whoFixes}
          </div>
          <p className="mt-0.5 text-gray-800">{t.roleHeaders[gap.routed_to]}</p>
          {gap.routed_to === "promoter" ? (
            <Link
              to={`/wizard?focus=${encodeURIComponent(gap.missing_fact_key)}`}
              className="mt-2 inline-block rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
            >
              {t.fixInWizard}
            </Link>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function SummaryStrip({
  counts,
  total,
  t,
  coverage,
}: {
  counts: Counts;
  total: number;
  t: (typeof UI)[Lang];
  coverage: CoverageReport | null;
}) {
  const pct = useMemo(() => {
    if (!coverage) return null;
    const covered = coverage.sections.reduce((a, s) => a + s.covered, 0);
    const totalEntries = coverage.sections.reduce((a, s) => a + s.total, 0);
    return totalEntries ? Math.round((100 * covered) / totalEntries) : 0;
  }, [coverage]);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="text-2xl font-semibold text-gray-900">{total}</div>
          <div className="text-xs uppercase tracking-wide text-gray-500">
            {t.openGaps(total)}
          </div>
        </div>
        <div className="text-xs text-gray-500">{t.gapExplainer}</div>
      </div>

      {pct !== null ? (
        <div className="mt-4">
          <div className="mb-1 flex justify-between text-sm text-gray-700">
            <span>{t.coverageLabel}</span>
            <span className="font-semibold">{pct}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded bg-gray-200">
            <div
              className="h-full bg-green-500 transition-all"
              style={{ width: `${pct}%` }}
              aria-hidden
            />
          </div>
          <p className="mt-1 text-xs text-gray-500">{t.coverageNote}</p>
        </div>
      ) : null}

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
            <div className="text-xs text-gray-600">{t.roleHeaders[role]}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RoleGroup({
  role,
  gaps,
  t,
  entriesById,
  hindiByKey,
  expanded,
  onToggle,
}: {
  role: Role;
  gaps: Gap[];
  t: (typeof UI)[Lang];
  entriesById: Map<string, ChecklistEntry>;
  hindiByKey: Map<string, string> | null;
  expanded: Record<string, boolean>;
  onToggle: (rowKey: string) => void;
}) {
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
          {t.roleHeaders[role]}
        </h2>
        <span className="text-xs text-gray-500">
          {gaps.length} {t.items(gaps.length)}
        </span>
      </header>
      <p className={"text-sm text-gray-600 " + (compact ? "mb-2" : "mb-4")}>
        {t.roleBlurbs[role]}
      </p>
      <ul className={compact ? "text-sm" : ""}>
        {gaps.map((g) => {
          const rowKey = `${g.entry_id}::${g.missing_fact_key}`;
          return (
            <GapRow
              key={rowKey}
              gap={g}
              t={t}
              entry={entriesById.get(g.entry_id)}
              hindiLabel={hindiByKey?.get(g.missing_fact_key)}
              expanded={!!expanded[rowKey]}
              onToggle={() => onToggle(rowKey)}
            />
          );
        })}
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
  const [lang, setLang] = useState<Lang>("en");
  const t = UI[lang];
  const [entriesById, setEntriesById] = useState<Map<string, ChecklistEntry>>(new Map());
  const [coverage, setCoverage] = useState<CoverageReport | null>(null);
  const [hindiByKey, setHindiByKey] = useState<Map<string, string> | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

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
    // Non-blocking enrichments; failures render nothing rather than break the page.
    getSchema()
      .then((c) => setEntriesById(new Map(c.entries.map((e) => [e.id, e]))))
      .catch(() => undefined);
    getCoverage()
      .then(setCoverage)
      .catch(() => undefined);
  }, [load]);

  // Hindi fact-key labels reuse the wizard's question copy — no new content.
  useEffect(() => {
    if (lang !== "hi" || hindiByKey !== null) return;
    getWizardQuestions("hi")
      .then((qs) => setHindiByKey(new Map(qs.map((q) => [q.fact_key, q.prompt]))))
      .catch(() => setHindiByKey(new Map()));
  }, [lang, hindiByKey]);

  const grouped = useMemo(() => {
    if (state.kind !== "ready") return null;
    return groupByRole(state.report.gaps);
  }, [state]);

  const counts = useMemo(() => {
    if (state.kind !== "ready") return null;
    return tally(state.report.gaps);
  }, [state]);

  const isLoading = state.kind === "loading";

  const onToggleRow = useCallback(
    (rowKey: string) => setExpanded((prev) => ({ ...prev, [rowKey]: !prev[rowKey] })),
    [],
  );

  return (
    <section className="mx-auto max-w-4xl space-y-6 p-2">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">{t.title}</h1>
          <p className="mt-1 max-w-2xl text-gray-600">{t.intro}</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex overflow-hidden rounded border text-sm" role="group">
            <button
              type="button"
              onClick={() => setLang("en")}
              aria-pressed={lang === "en"}
              className={
                "px-3 py-1.5 " +
                (lang === "en" ? "bg-gray-900 text-white" : "bg-white text-gray-700 hover:bg-gray-100")
              }
            >
              EN
            </button>
            <button
              type="button"
              onClick={() => setLang("hi")}
              aria-pressed={lang === "hi"}
              className={
                "px-3 py-1.5 " +
                (lang === "hi" ? "bg-gray-900 text-white" : "bg-white text-gray-700 hover:bg-gray-100")
              }
            >
              हिंदी
            </button>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            disabled={isLoading}
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isLoading ? t.refreshing : t.refresh}
          </button>
        </div>
      </header>

      {state.kind === "loading" && (
        <div
          role="status"
          aria-live="polite"
          className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-600"
        >
          {t.loading}
        </div>
      )}

      {state.kind === "error" && (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
        >
          <div className="font-medium">{t.loadErrorTitle}</div>
          <div className="mt-1 text-red-700">{state.message}</div>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-3 rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-800 hover:bg-red-100"
          >
            {t.retry}
          </button>
        </div>
      )}

      {state.kind === "ready" && counts && grouped && (
        <>
          <SummaryStrip
            counts={counts}
            total={state.report.gaps.length}
            t={t}
            coverage={coverage}
          />

          {state.report.gaps.length === 0 ? (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-6 text-center">
              <div className="text-lg font-semibold text-emerald-900">{t.emptyTitle}</div>
              <p className="mt-1 text-sm text-emerald-800">{t.emptySub}</p>
            </div>
          ) : (
            <div className="space-y-4">
              {ROLE_ORDER.map((role) => (
                <RoleGroup
                  key={role}
                  role={role}
                  gaps={grouped[role]}
                  t={t}
                  entriesById={entriesById}
                  hindiByKey={lang === "hi" ? hindiByKey : null}
                  expanded={expanded}
                  onToggle={onToggleRow}
                />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
