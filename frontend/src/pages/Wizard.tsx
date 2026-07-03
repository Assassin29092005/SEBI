import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  acceptProposal,
  confirmFact,
  getWizardQuestions,
  postFact,
  uploadExtract,
  formatPaise,
} from "../api/client";
import type {
  ExtractionProposal,
  Fact,
  WizardQuestion,
} from "../api/client";

// ---------------------------------------------------------------------------
// Language toggle
// ---------------------------------------------------------------------------

type Lang = "en" | "hi";

const UI_COPY: Record<Lang, {
  title: string;
  intro: string;
  tabQuestions: string;
  tabUploads: string;
  progress: (a: number, t: number) => string;
  whyExpand: string;
  whyCollapse: string;
  clauseChip: string;
  save: string;
  saving: string;
  savedEcho: string;
  confirm: string;
  confirming: string;
  confirmed: string;
  editAgain: string;
  fieldRequired: string;
  moneyLabel: string;
  moneyHint: string;
  listPlaceholder: string;
  listAdd: string;
  listRemove: string;
  loading: string;
  loadError: string;
  retry: string;
  uploadHeading: string;
  uploadIntro: string;
  uploadUnconfirmedWarn: string;
  uploadPick: string;
  uploadRunning: string;
  uploadError: string;
  uploadEmpty: string;
  proposalConfidence: string;
  proposalFoundOn: string;
  proposalConfirm: string;
  proposalReject: string;
  proposalAccepting: string;
  proposalAccepted: string;
  proposalRejected: string;
  footerNext: string;
}> = {
  en: {
    title: "Guided Wizard",
    intro:
      "Tell us about your business, or upload documents you already have. " +
      "We will extract details and ask you to confirm each one before it enters your draft.",
    tabQuestions: "Answer questions",
    tabUploads: "Upload documents",
    progress: (a, t) => `${a} of ${t} confirmed`,
    whyExpand: "Why we ask this",
    whyCollapse: "Hide explanation",
    clauseChip: "Clause",
    save: "Save answer",
    saving: "Saving…",
    savedEcho: "You entered:",
    confirm: "Confirm",
    confirming: "Confirming…",
    confirmed: "Confirmed",
    editAgain: "Edit again",
    fieldRequired: "Please enter a value before saving.",
    moneyLabel: "Amount in rupees (₹)",
    moneyHint: "Stored as paise. Enter whole rupees; no decimals.",
    listPlaceholder: "Add an item and press Enter",
    listAdd: "Add",
    listRemove: "Remove",
    loading: "Loading questions…",
    loadError: "We could not load the questions.",
    retry: "Try again",
    uploadHeading: "Upload a document",
    uploadIntro:
      "PDFs and text files work. We extract candidate facts and show you " +
      "the exact snippet each one came from.",
    uploadUnconfirmedWarn:
      "Extracted values are proposals. They never enter your draft until you confirm them.",
    uploadPick: "Choose a file",
    uploadRunning: "Extracting…",
    uploadError: "Extraction failed.",
    uploadEmpty: "No facts were extracted from this file.",
    proposalConfidence: "Confidence",
    proposalFoundOn: "Found on page",
    proposalConfirm: "Confirm this fact",
    proposalReject: "Reject",
    proposalAccepting: "Accepting…",
    proposalAccepted: "Confirmed and added to your fact store.",
    proposalRejected: "Discarded. This value will not enter your draft.",
    footerNext: "Continue to your gap report",
  },
  hi: {
    title: "गाइडेड विज़ार्ड",
    intro:
      "अपने व्यवसाय के बारे में बताएं, या पहले से मौजूद दस्तावेज़ अपलोड करें। " +
      "हम विवरण निकालेंगे और मसौदे में जोड़ने से पहले हर एक की पुष्टि आपसे कराएंगे।",
    tabQuestions: "प्रश्नों के उत्तर दें",
    tabUploads: "दस्तावेज़ अपलोड करें",
    progress: (a, t) => `${t} में से ${a} पुष्टि किए गए`,
    whyExpand: "यह क्यों पूछा जा रहा है",
    whyCollapse: "स्पष्टीकरण छिपाएँ",
    clauseChip: "खंड",
    save: "उत्तर सहेजें",
    saving: "सहेजा जा रहा है…",
    savedEcho: "आपने दर्ज किया:",
    confirm: "पुष्टि करें",
    confirming: "पुष्टि की जा रही है…",
    confirmed: "पुष्टि हो गई",
    editAgain: "फिर से संपादित करें",
    fieldRequired: "सहेजने से पहले मान दर्ज करें।",
    moneyLabel: "राशि रुपये में (₹)",
    moneyHint: "पैसे में संग्रहीत। पूर्ण रुपये दर्ज करें; दशमलव नहीं।",
    listPlaceholder: "एक आइटम जोड़ें और Enter दबाएँ",
    listAdd: "जोड़ें",
    listRemove: "हटाएँ",
    loading: "प्रश्न लोड हो रहे हैं…",
    loadError: "प्रश्न लोड नहीं हो सके।",
    retry: "फिर कोशिश करें",
    uploadHeading: "एक दस्तावेज़ अपलोड करें",
    uploadIntro:
      "PDF और टेक्स्ट फ़ाइलें काम करती हैं। हम संभावित तथ्य निकालते हैं " +
      "और वह वाक्यांश दिखाते हैं जहाँ से हर एक आया है।",
    uploadUnconfirmedWarn:
      "निकाले गए मान केवल प्रस्ताव हैं। जब तक आप पुष्टि नहीं करते, वे मसौदे में नहीं जाते।",
    uploadPick: "फ़ाइल चुनें",
    uploadRunning: "निकाला जा रहा है…",
    uploadError: "निष्कर्षण विफल हुआ।",
    uploadEmpty: "इस फ़ाइल से कोई तथ्य नहीं निकाला जा सका।",
    proposalConfidence: "विश्वास",
    proposalFoundOn: "इस पृष्ठ पर मिला",
    proposalConfirm: "इस तथ्य की पुष्टि करें",
    proposalReject: "अस्वीकार करें",
    proposalAccepting: "स्वीकार किया जा रहा है…",
    proposalAccepted: "पुष्टि की गई और आपके फैक्ट स्टोर में जोड़ी गई।",
    proposalRejected: "त्याग दिया गया। यह मान मसौदे में नहीं जाएगा।",
    footerNext: "अपनी गैप रिपोर्ट पर जारी रखें",
  },
};

// ---------------------------------------------------------------------------
// Money parsing (integer-only, whole rupees → paise)
// ---------------------------------------------------------------------------

function parseRupeesToPaise(raw: string): number | null {
  const cleaned = raw.replace(/[,\s₹]/g, "");
  if (!cleaned) return null;
  if (!/^\d+$/.test(cleaned)) return null;
  const rupees = Number(cleaned);
  if (!Number.isSafeInteger(rupees) || rupees < 0) return null;
  return rupees * 100;
}

// ---------------------------------------------------------------------------
// Per-question local state
// ---------------------------------------------------------------------------

type Stage = "editing" | "saving" | "awaiting_confirm" | "confirming" | "confirmed" | "error";

interface QuestionState {
  textValue: string;
  listValue: string[];
  savedFact: Fact | null;
  stage: Stage;
  errorMessage: string | null;
}

function emptyQuestionState(): QuestionState {
  return {
    textValue: "",
    listValue: [],
    savedFact: null,
    stage: "editing",
    errorMessage: null,
  };
}

// ---------------------------------------------------------------------------
// Per-proposal local state (upload path)
// ---------------------------------------------------------------------------

type ProposalStage = "pending" | "accepting" | "confirming" | "confirmed" | "rejected" | "error";

interface ProposalState {
  stage: ProposalStage;
  errorMessage: string | null;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type Tab = "questions" | "uploads";

export default function Wizard() {
  const [lang, setLang] = useState<Lang>("en");
  const t = UI_COPY[lang];
  const [tab, setTab] = useState<Tab>("questions");

  // -------- Questions state --------
  const [questions, setQuestions] = useState<WizardQuestion[] | null>(null);
  const [questionsLoading, setQuestionsLoading] = useState<boolean>(false);
  const [questionsError, setQuestionsError] = useState<string | null>(null);
  const [states, setStates] = useState<Record<string, QuestionState>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // -------- Upload state --------
  const [uploading, setUploading] = useState<boolean>(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadedFile, setUploadedFile] = useState<string | null>(null);
  const [proposals, setProposals] = useState<ExtractionProposal[]>([]);
  const [proposalStates, setProposalStates] = useState<Record<number, ProposalState>>({});

  // -------- Load questions on lang change --------
  useEffect(() => {
    let cancelled = false;
    setQuestionsLoading(true);
    setQuestionsError(null);
    getWizardQuestions(lang)
      .then((qs) => {
        if (cancelled) return;
        setQuestions(qs);
        // Preserve prior answers on language switch, but drop states for
        // questions that no longer exist in the new list.
        setStates((prev) => {
          const next: Record<string, QuestionState> = {};
          for (const q of qs) {
            next[q.fact_key] = prev[q.fact_key] ?? emptyQuestionState();
          }
          return next;
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setQuestionsError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setQuestionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [lang]);

  // -------- Grouping + progress --------
  const grouped = useMemo(() => {
    const map = new Map<string, WizardQuestion[]>();
    for (const q of questions ?? []) {
      const list = map.get(q.section) ?? [];
      list.push(q);
      map.set(q.section, list);
    }
    return Array.from(map.entries());
  }, [questions]);

  const progress = useMemo(() => {
    const total = questions?.length ?? 0;
    const confirmed = Object.values(states).filter((s) => s.stage === "confirmed").length;
    return { total, confirmed };
  }, [questions, states]);

  // -------- Helpers to mutate a single question's state --------
  function updateState(factKey: string, patch: Partial<QuestionState>) {
    setStates((prev) => ({
      ...prev,
      [factKey]: { ...(prev[factKey] ?? emptyQuestionState()), ...patch },
    }));
  }

  type BuildResult = { ok: true; value: unknown } | { ok: false; error: string };

  function buildValue(q: WizardQuestion, s: QuestionState): BuildResult {
    if (q.input_hint === "money") {
      const paise = parseRupeesToPaise(s.textValue);
      if (paise === null) return { ok: false, error: t.fieldRequired };
      return { ok: true, value: paise };
    }
    if (q.input_hint === "list") {
      if (s.listValue.length === 0) return { ok: false, error: t.fieldRequired };
      return { ok: true, value: s.listValue };
    }
    if (q.input_hint === "date") {
      if (!s.textValue) return { ok: false, error: t.fieldRequired };
      return { ok: true, value: s.textValue };
    }
    // text / anything else
    if (!s.textValue.trim()) return { ok: false, error: t.fieldRequired };
    return { ok: true, value: s.textValue.trim() };
  }

  async function handleSave(q: WizardQuestion) {
    const s = states[q.fact_key] ?? emptyQuestionState();
    const built = buildValue(q, s);
    if (!built.ok) {
      updateState(q.fact_key, { stage: "error", errorMessage: built.error });
      return;
    }
    updateState(q.fact_key, { stage: "saving", errorMessage: null });
    const draft: Fact = {
      // fact_id is the client-supplied identifier; the backend Fact model
      // requires it as a string field. crypto.randomUUID is available on
      // all modern browsers (lib.dom); no polyfill needed.
      fact_id: crypto.randomUUID(),
      key: q.fact_key,
      value: built.value,
      provenance: {
        kind: "wizard",
        detail: `q:${q.fact_key}`,
        snippet: null,
        supersedes: null,
      },
      confidence: 1,
      confirmed: false,
      supplied_by: "promoter",
      created_at: new Date().toISOString(),
    };
    try {
      const saved = await postFact(draft);
      updateState(q.fact_key, {
        stage: "awaiting_confirm",
        savedFact: saved,
        errorMessage: null,
      });
    } catch (err: unknown) {
      updateState(q.fact_key, {
        stage: "error",
        errorMessage: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function handleConfirm(q: WizardQuestion) {
    const s = states[q.fact_key];
    if (!s?.savedFact) return;
    updateState(q.fact_key, { stage: "confirming", errorMessage: null });
    try {
      const confirmed = await confirmFact(s.savedFact.fact_id);
      updateState(q.fact_key, { stage: "confirmed", savedFact: confirmed });
    } catch (err: unknown) {
      updateState(q.fact_key, {
        stage: "error",
        errorMessage: err instanceof Error ? err.message : String(err),
      });
    }
  }

  function handleEditAgain(q: WizardQuestion) {
    updateState(q.fact_key, {
      stage: "editing",
      savedFact: null,
      errorMessage: null,
    });
  }

  // -------- Upload handlers --------
  async function handleUpload(file: File) {
    setUploading(true);
    setUploadError(null);
    setUploadedFile(file.name);
    setProposals([]);
    setProposalStates({});
    try {
      const result = await uploadExtract(file);
      setProposals(result);
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }

  async function handleAcceptProposal(idx: number, p: ExtractionProposal) {
    setProposalStates((prev) => ({
      ...prev,
      [idx]: { stage: "accepting", errorMessage: null },
    }));
    try {
      const fact = await acceptProposal(p);
      setProposalStates((prev) => ({
        ...prev,
        [idx]: { stage: "confirming", errorMessage: null },
      }));
      await confirmFact(fact.fact_id);
      setProposalStates((prev) => ({
        ...prev,
        [idx]: { stage: "confirmed", errorMessage: null },
      }));
    } catch (err: unknown) {
      setProposalStates((prev) => ({
        ...prev,
        [idx]: {
          stage: "error",
          errorMessage: err instanceof Error ? err.message : String(err),
        },
      }));
    }
  }

  function handleRejectProposal(idx: number) {
    setProposalStates((prev) => ({
      ...prev,
      [idx]: { stage: "rejected", errorMessage: null },
    }));
  }

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  return (
    <section>
      <div className="flex items-start justify-between mb-4 gap-4">
        <div>
          <h1 className="text-2xl font-semibold mb-1">{t.title}</h1>
          <p className="text-gray-600 max-w-2xl">{t.intro}</p>
        </div>
        <div className="shrink-0">
          <LangToggle lang={lang} onChange={setLang} />
        </div>
      </div>

      <div className="flex gap-2 border-b mb-6">
        <TabButton active={tab === "questions"} onClick={() => setTab("questions")}>
          {t.tabQuestions}
        </TabButton>
        <TabButton active={tab === "uploads"} onClick={() => setTab("uploads")}>
          {t.tabUploads}
        </TabButton>
      </div>

      {tab === "questions" && (
        <QuestionsPanel
          t={t}
          loading={questionsLoading}
          error={questionsError}
          onRetry={() => setLang(lang)} // trigger re-fetch via effect
          grouped={grouped}
          progress={progress}
          states={states}
          expanded={expanded}
          setExpanded={setExpanded}
          onTextChange={(key, v) => updateState(key, { textValue: v })}
          onListChange={(key, list) => updateState(key, { listValue: list })}
          onSave={handleSave}
          onConfirm={handleConfirm}
          onEditAgain={handleEditAgain}
        />
      )}

      {tab === "uploads" && (
        <UploadPanel
          t={t}
          uploading={uploading}
          uploadError={uploadError}
          uploadedFile={uploadedFile}
          proposals={proposals}
          proposalStates={proposalStates}
          onUpload={handleUpload}
          onAccept={handleAcceptProposal}
          onReject={handleRejectProposal}
        />
      )}

      <footer className="mt-10 pt-6 border-t">
        <Link
          to="/gaps"
          className="inline-flex items-center gap-2 text-blue-700 hover:text-blue-900 font-medium"
        >
          {t.footerNext} →
        </Link>
      </footer>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Small presentational components
// ---------------------------------------------------------------------------

function LangToggle({ lang, onChange }: { lang: Lang; onChange: (l: Lang) => void }) {
  return (
    <div className="inline-flex rounded border overflow-hidden text-sm" role="group">
      <button
        type="button"
        onClick={() => onChange("en")}
        className={
          "px-3 py-1 " +
          (lang === "en" ? "bg-gray-900 text-white" : "bg-white text-gray-700 hover:bg-gray-100")
        }
        aria-pressed={lang === "en"}
      >
        EN
      </button>
      <button
        type="button"
        onClick={() => onChange("hi")}
        className={
          "px-3 py-1 border-l " +
          (lang === "hi" ? "bg-gray-900 text-white" : "bg-white text-gray-700 hover:bg-gray-100")
        }
        aria-pressed={lang === "hi"}
      >
        हिंदी
      </button>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "px-4 py-2 text-sm font-medium border-b-2 -mb-px " +
        (active
          ? "border-blue-600 text-blue-700"
          : "border-transparent text-gray-500 hover:text-gray-800")
      }
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Questions panel
// ---------------------------------------------------------------------------

interface QuestionsPanelProps {
  t: (typeof UI_COPY)[Lang];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  grouped: [string, WizardQuestion[]][];
  progress: { confirmed: number; total: number };
  states: Record<string, QuestionState>;
  expanded: Record<string, boolean>;
  setExpanded: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  onTextChange: (factKey: string, value: string) => void;
  onListChange: (factKey: string, list: string[]) => void;
  onSave: (q: WizardQuestion) => void;
  onConfirm: (q: WizardQuestion) => void;
  onEditAgain: (q: WizardQuestion) => void;
}

function QuestionsPanel(props: QuestionsPanelProps) {
  const { t, loading, error, onRetry, grouped, progress, states, expanded, setExpanded } = props;

  if (loading) {
    return <p className="text-gray-500">{t.loading}</p>;
  }
  if (error) {
    return (
      <div className="rounded border border-red-200 bg-red-50 p-4">
        <p className="text-red-800 font-medium mb-2">{t.loadError}</p>
        <p className="text-xs text-red-700 mb-3 break-all">{error}</p>
        <button
          type="button"
          onClick={onRetry}
          className="px-3 py-1 rounded bg-red-700 text-white text-sm hover:bg-red-800"
        >
          {t.retry}
        </button>
      </div>
    );
  }
  if (grouped.length === 0) {
    return <p className="text-gray-500">—</p>;
  }

  const pct = progress.total === 0 ? 0 : Math.round((progress.confirmed / progress.total) * 100);

  return (
    <div>
      <div className="mb-6">
        <div className="flex justify-between text-sm text-gray-700 mb-1">
          <span>{t.progress(progress.confirmed, progress.total)}</span>
          <span>{pct}%</span>
        </div>
        <div className="h-2 bg-gray-200 rounded overflow-hidden">
          <div
            className="h-full bg-green-500 transition-all"
            style={{ width: `${pct}%` }}
            aria-hidden
          />
        </div>
      </div>

      <div className="space-y-8">
        {grouped.map(([section, sectionQs]) => (
          <div key={section}>
            <h2 className="text-lg font-semibold text-gray-900 mb-3">{section}</h2>
            <div className="space-y-4">
              {sectionQs.map((q) => (
                <QuestionCard
                  key={q.fact_key}
                  t={t}
                  q={q}
                  state={states[q.fact_key] ?? emptyQuestionState()}
                  expanded={!!expanded[q.fact_key]}
                  toggleExpand={() =>
                    setExpanded((prev) => ({ ...prev, [q.fact_key]: !prev[q.fact_key] }))
                  }
                  onTextChange={(v) => props.onTextChange(q.fact_key, v)}
                  onListChange={(list) => props.onListChange(q.fact_key, list)}
                  onSave={() => props.onSave(q)}
                  onConfirm={() => props.onConfirm(q)}
                  onEditAgain={() => props.onEditAgain(q)}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One question
// ---------------------------------------------------------------------------

interface QuestionCardProps {
  t: (typeof UI_COPY)[Lang];
  q: WizardQuestion;
  state: QuestionState;
  expanded: boolean;
  toggleExpand: () => void;
  onTextChange: (value: string) => void;
  onListChange: (list: string[]) => void;
  onSave: () => void;
  onConfirm: () => void;
  onEditAgain: () => void;
}

function QuestionCard(props: QuestionCardProps) {
  const { t, q, state, expanded, toggleExpand } = props;
  const locked =
    state.stage === "saving" ||
    state.stage === "awaiting_confirm" ||
    state.stage === "confirming" ||
    state.stage === "confirmed";

  return (
    <div className="rounded border bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-4 mb-2">
        <p className="font-medium text-gray-900">{q.prompt}</p>
        {state.stage === "confirmed" && (
          <span className="shrink-0 rounded-full bg-green-100 text-green-800 text-xs px-2 py-0.5">
            {t.confirmed}
          </span>
        )}
      </div>

      <button
        type="button"
        onClick={toggleExpand}
        className="text-xs text-blue-700 hover:underline"
      >
        {expanded ? t.whyCollapse : t.whyExpand}
      </button>
      {expanded && (
        <div className="mt-2 rounded bg-gray-50 border border-gray-200 p-3 text-sm text-gray-700">
          <p className="mb-2">{q.why_we_ask}</p>
          <span className="inline-block rounded bg-white border text-xs px-2 py-0.5 text-gray-700">
            {t.clauseChip}: {q.clause_ref}
          </span>
          {q.help_text && <p className="mt-2 text-xs text-gray-500">{q.help_text}</p>}
        </div>
      )}

      <div className="mt-3">
        {!locked && (
          <InputWidget
            t={t}
            hint={q.input_hint}
            textValue={state.textValue}
            listValue={state.listValue}
            onTextChange={props.onTextChange}
            onListChange={props.onListChange}
          />
        )}

        {state.stage === "awaiting_confirm" && state.savedFact && (
          <ConfirmPanel t={t} q={q} fact={state.savedFact} />
        )}

        {state.stage === "confirmed" && state.savedFact && (
          <div className="rounded border border-green-200 bg-green-50 p-3 text-sm text-green-900">
            <span className="font-medium">{t.savedEcho}</span>{" "}
            <span>{renderValue(q, state.savedFact.value)}</span>
          </div>
        )}

        {state.errorMessage && (
          <p className="mt-2 text-sm text-red-700">{state.errorMessage}</p>
        )}

        <div className="mt-3 flex gap-2 flex-wrap">
          {state.stage === "editing" && (
            <button
              type="button"
              onClick={props.onSave}
              className="rounded bg-blue-600 text-white text-sm px-3 py-1.5 hover:bg-blue-700"
            >
              {t.save}
            </button>
          )}
          {state.stage === "saving" && (
            <button
              type="button"
              disabled
              className="rounded bg-blue-400 text-white text-sm px-3 py-1.5"
            >
              {t.saving}
            </button>
          )}
          {state.stage === "awaiting_confirm" && (
            <>
              <button
                type="button"
                onClick={props.onConfirm}
                className="rounded bg-green-600 text-white text-sm px-3 py-1.5 hover:bg-green-700"
              >
                {t.confirm}
              </button>
              <button
                type="button"
                onClick={props.onEditAgain}
                className="rounded border text-sm px-3 py-1.5 text-gray-700 hover:bg-gray-50"
              >
                {t.editAgain}
              </button>
            </>
          )}
          {state.stage === "confirming" && (
            <button
              type="button"
              disabled
              className="rounded bg-green-400 text-white text-sm px-3 py-1.5"
            >
              {t.confirming}
            </button>
          )}
          {state.stage === "confirmed" && (
            <button
              type="button"
              onClick={props.onEditAgain}
              className="rounded border text-sm px-3 py-1.5 text-gray-700 hover:bg-gray-50"
            >
              {t.editAgain}
            </button>
          )}
          {state.stage === "error" && (
            <button
              type="button"
              onClick={props.onSave}
              className="rounded bg-blue-600 text-white text-sm px-3 py-1.5 hover:bg-blue-700"
            >
              {t.retry}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function ConfirmPanel({
  t,
  q,
  fact,
}: {
  t: (typeof UI_COPY)[Lang];
  q: WizardQuestion;
  fact: Fact;
}) {
  return (
    <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm">
      <p className="text-amber-900">
        <span className="font-medium">{t.savedEcho}</span>{" "}
        <span>{renderValue(q, fact.value)}</span>
      </p>
    </div>
  );
}

// Render a fact value for display, respecting the money → paise convention.
function renderValue(q: WizardQuestion, value: unknown): string {
  if (q.input_hint === "money" && typeof value === "number" && Number.isInteger(value)) {
    return formatPaise(value);
  }
  if (Array.isArray(value)) {
    return value.map((v) => String(v)).join(", ");
  }
  if (value === null || value === undefined) return "—";
  return String(value);
}

// ---------------------------------------------------------------------------
// Input widgets
// ---------------------------------------------------------------------------

interface InputWidgetProps {
  t: (typeof UI_COPY)[Lang];
  hint: string;
  textValue: string;
  listValue: string[];
  onTextChange: (v: string) => void;
  onListChange: (list: string[]) => void;
}

function InputWidget({ t, hint, textValue, listValue, onTextChange, onListChange }: InputWidgetProps) {
  if (hint === "money") {
    const preview = parseRupeesToPaise(textValue);
    return (
      <div>
        <label className="block text-xs text-gray-600 mb-1">{t.moneyLabel}</label>
        <div className="flex items-center gap-2">
          <span className="text-gray-500">₹</span>
          <input
            type="text"
            inputMode="numeric"
            value={textValue}
            onChange={(e) => onTextChange(e.target.value)}
            className="border rounded px-2 py-1 flex-1"
            placeholder="0"
          />
        </div>
        <p className="mt-1 text-xs text-gray-500">
          {t.moneyHint}
          {preview !== null && <> · {formatPaise(preview)}</>}
        </p>
      </div>
    );
  }

  if (hint === "list") {
    return (
      <ListInput
        t={t}
        items={listValue}
        draft={textValue}
        onDraftChange={onTextChange}
        onItemsChange={onListChange}
      />
    );
  }

  if (hint === "date") {
    return (
      <input
        type="date"
        value={textValue}
        onChange={(e) => onTextChange(e.target.value)}
        className="border rounded px-2 py-1"
      />
    );
  }

  return (
    <input
      type="text"
      value={textValue}
      onChange={(e) => onTextChange(e.target.value)}
      className="border rounded px-2 py-1 w-full"
    />
  );
}

function ListInput({
  t,
  items,
  draft,
  onDraftChange,
  onItemsChange,
}: {
  t: (typeof UI_COPY)[Lang];
  items: string[];
  draft: string;
  onDraftChange: (v: string) => void;
  onItemsChange: (list: string[]) => void;
}) {
  function addItem() {
    const trimmed = draft.trim();
    if (!trimmed) return;
    onItemsChange([...items, trimmed]);
    onDraftChange("");
  }
  function removeItem(idx: number) {
    onItemsChange(items.filter((_, i) => i !== idx));
  }
  return (
    <div>
      {items.length > 0 && (
        <ul className="mb-2 space-y-1">
          {items.map((item, idx) => (
            <li
              key={`${idx}-${item}`}
              className="flex items-center justify-between rounded bg-gray-50 border px-2 py-1 text-sm"
            >
              <span>{item}</span>
              <button
                type="button"
                onClick={() => removeItem(idx)}
                className="text-xs text-red-700 hover:underline"
              >
                {t.listRemove}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addItem();
            }
          }}
          placeholder={t.listPlaceholder}
          className="flex-1 border rounded px-2 py-1"
        />
        <button
          type="button"
          onClick={addItem}
          className="rounded border text-sm px-3 py-1 hover:bg-gray-50"
        >
          {t.listAdd}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Upload panel
// ---------------------------------------------------------------------------

interface UploadPanelProps {
  t: (typeof UI_COPY)[Lang];
  uploading: boolean;
  uploadError: string | null;
  uploadedFile: string | null;
  proposals: ExtractionProposal[];
  proposalStates: Record<number, ProposalState>;
  onUpload: (file: File) => void;
  onAccept: (idx: number, p: ExtractionProposal) => void;
  onReject: (idx: number) => void;
}

function UploadPanel(props: UploadPanelProps) {
  const { t, uploading, uploadError, uploadedFile, proposals, proposalStates } = props;

  return (
    <div>
      <h2 className="text-lg font-semibold text-gray-900 mb-2">{t.uploadHeading}</h2>
      <p className="text-gray-600 mb-3 text-sm">{t.uploadIntro}</p>
      <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 mb-4">
        {t.uploadUnconfirmedWarn}
      </div>

      <label className="inline-flex items-center gap-3 cursor-pointer">
        <span className="rounded bg-blue-600 text-white text-sm px-3 py-1.5 hover:bg-blue-700">
          {t.uploadPick}
        </span>
        <input
          type="file"
          className="hidden"
          disabled={uploading}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) props.onUpload(file);
            // Reset input so the same file can be re-selected if needed.
            e.target.value = "";
          }}
        />
        {uploadedFile && !uploading && (
          <span className="text-sm text-gray-600">{uploadedFile}</span>
        )}
      </label>

      {uploading && <p className="mt-3 text-sm text-gray-500">{t.uploadRunning}</p>}
      {uploadError && (
        <div className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm">
          <p className="text-red-800 font-medium">{t.uploadError}</p>
          <p className="text-xs text-red-700 break-all">{uploadError}</p>
        </div>
      )}

      {!uploading && uploadedFile && proposals.length === 0 && !uploadError && (
        <p className="mt-4 text-sm text-gray-500">{t.uploadEmpty}</p>
      )}

      {proposals.length > 0 && (
        <div className="mt-6 space-y-4">
          {proposals.map((p, idx) => (
            <ProposalCard
              key={`${p.fact_key}-${idx}`}
              t={t}
              p={p}
              state={proposalStates[idx] ?? { stage: "pending", errorMessage: null }}
              onAccept={() => props.onAccept(idx, p)}
              onReject={() => props.onReject(idx)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ProposalCard({
  t,
  p,
  state,
  onAccept,
  onReject,
}: {
  t: (typeof UI_COPY)[Lang];
  p: ExtractionProposal;
  state: ProposalState;
  onAccept: () => void;
  onReject: () => void;
}) {
  const isMoneyKey = p.fact_key.endsWith("_paise");
  const displayValue =
    isMoneyKey && typeof p.value === "number" && Number.isInteger(p.value)
      ? formatPaise(p.value)
      : String(p.value);

  const confidencePct = Math.round(p.confidence * 100);
  const confidenceTone =
    confidencePct >= 80
      ? "bg-green-100 text-green-800"
      : confidencePct >= 50
        ? "bg-amber-100 text-amber-900"
        : "bg-red-100 text-red-800";

  const inFlight = state.stage === "accepting" || state.stage === "confirming";

  return (
    <div className="rounded border bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div>
          <p className="text-xs uppercase tracking-wide text-gray-500">{p.fact_key}</p>
          <p className="text-lg font-semibold text-gray-900">{displayValue}</p>
        </div>
        <span className={`shrink-0 rounded-full text-xs px-2 py-0.5 ${confidenceTone}`}>
          {t.proposalConfidence}: {confidencePct}%
        </span>
      </div>

      <div className="rounded bg-yellow-50 border border-yellow-200 p-3 text-sm text-gray-800">
        <p className="mb-1 text-xs text-gray-600">
          {t.proposalFoundOn} {p.page} · {p.source_file}
        </p>
        <p className="italic">“{p.snippet}”</p>
      </div>

      {state.stage === "confirmed" && (
        <p className="mt-3 text-sm text-green-800">{t.proposalAccepted}</p>
      )}
      {state.stage === "rejected" && (
        <p className="mt-3 text-sm text-gray-500">{t.proposalRejected}</p>
      )}
      {state.stage === "error" && state.errorMessage && (
        <p className="mt-3 text-sm text-red-700">{state.errorMessage}</p>
      )}

      {(state.stage === "pending" || state.stage === "error") && (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={onAccept}
            className="rounded bg-green-600 text-white text-sm px-3 py-1.5 hover:bg-green-700"
          >
            {t.proposalConfirm}
          </button>
          <button
            type="button"
            onClick={onReject}
            className="rounded border text-sm px-3 py-1.5 text-gray-700 hover:bg-gray-50"
          >
            {t.proposalReject}
          </button>
        </div>
      )}
      {inFlight && (
        <p className="mt-3 text-sm text-gray-500">{t.proposalAccepting}</p>
      )}
    </div>
  );
}
