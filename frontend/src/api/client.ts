// Typed fetch wrapper + Pydantic mirror. All requests go through the Vite
// /api proxy in dev. Field names must match the backend models exactly —
// this file is the single source of truth on the frontend side.

// --------------------------------------------------------------------------
// Auth token — every request below attaches it as a Bearer header. Every
// backend endpoint except /api/health, /api/schema, and /api/auth/* now
// requires it (see backend/app/auth) — a 401 here means the token is
// missing/expired, so we clear it and let AuthContext bounce to /login.
// --------------------------------------------------------------------------

const TOKEN_STORAGE_KEY = "drhp_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token);
    else localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // Storage blocked — the token just won't persist across reloads.
  }
}

/** Fired when a request comes back 401 — AuthContext listens and logs out. */
function notifyUnauthorized(): void {
  setToken(null);
  window.dispatchEvent(new Event("auth:unauthorized"));
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// --------------------------------------------------------------------------
// Fetch helpers
// --------------------------------------------------------------------------

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { ...authHeaders() } });
  if (res.status === 401) notifyUnauthorized();
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (res.status === 401) notifyUnauthorized();
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

/**
 * Multipart POST helper — used for uploads. Do NOT set Content-Type manually:
 * the browser has to add the multipart boundary itself, and setting it here
 * would strip that.
 */
export async function apiUpload<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { ...authHeaders() },
    body: formData,
  });
  if (res.status === 401) notifyUnauthorized();
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

/**
 * Fetch a binary response (an image, an archived document's raw bytes) with
 * the same Authorization header every other request uses, and hand back an
 * object URL the caller can drop straight into <img src>. A plain
 * <img src="/api/..."> can't attach that header — every endpoint in this
 * app requires one (see backend/app/auth) — so pointing one directly at an
 * authenticated binary endpoint would 401 instead of rendering.
 *
 * The caller owns the returned URL: call revokeObjectUrl on it (typically
 * in a cleanup effect) once done, or the blob leaks for the page's lifetime.
 */
export async function apiGetBlobUrl(path: string): Promise<string> {
  const res = await fetch(path, { headers: { ...authHeaders() } });
  if (res.status === 401) notifyUnauthorized();
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export function revokeObjectUrl(url: string): void {
  URL.revokeObjectURL(url);
}

/** Same auth-header requirement as apiGetBlobUrl, but for text content
 * (a .txt source document) that the caller wants to search/highlight
 * client-side rather than display as an image. */
export async function apiGetText(path: string): Promise<string> {
  const res = await fetch(path, { headers: { ...authHeaders() } });
  if (res.status === 401) notifyUnauthorized();
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.text();
}

// --------------------------------------------------------------------------
// Enum-shaped string unions (mirror app.schema.models / app.facts / etc.)
// --------------------------------------------------------------------------

export type Role = "promoter" | "auditor" | "banker" | "system";
// Roles a human account can hold — "system" is a provenance tag only.
export type LoginRole = "promoter" | "auditor" | "banker";

// --------------------------------------------------------------------------
// Auth (backend/app/auth)
// --------------------------------------------------------------------------

export interface UserPublic {
  user_id: string;
  email: string;
  name: string;
  role: LoginRole;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: UserPublic;
}

export interface RegisterInput {
  email: string;
  name: string;
  password: string;
  role: LoginRole;
  invite_code?: string;
}

export interface LoginInput {
  email: string;
  password: string;
}

export const register = (input: RegisterInput): Promise<TokenResponse> =>
  apiPost<TokenResponse>("/api/auth/register", input);

export const login = (input: LoginInput): Promise<TokenResponse> =>
  apiPost<TokenResponse>("/api/auth/login", input);

export const getMe = (): Promise<UserPublic> => apiGet<UserPublic>("/api/auth/me");
export type Severity = "blocker" | "material" | "minor";
export type OutputTarget = "drhp" | "abridged";
export type SourceKind = "wizard" | "document" | "lookup" | "role_upload";
export type SectionState = "draft" | "reviewed" | "certified";
export type GateResult = "pass" | "fail";
export type ClaimKind = "number" | "entity" | "date";
// Mirrors FindingKind in backend/app/validate/arithmetic.py (a strict Literal).
export type ArithmeticFindingKind =
  | "objects_overallocated"
  | "unallocated_proceeds"
  | "gcp_cap_breach"
  | "missing_inputs";

// --------------------------------------------------------------------------
// Facts + provenance (backend/app/facts.py)
// --------------------------------------------------------------------------

export interface Provenance {
  kind: SourceKind;
  detail: string;
  snippet: string | null;
  supersedes: string | null;
  // Inline document viewer (backend/app/intake/vault + app.intake.uploads):
  // links back to the archived original. document_id/page are null for
  // anything not sourced from an upload (wizard answers, lookups) or when
  // archiving failed server-side (best-effort, see app.main.uploads_extract).
  document_id: string | null;
  page: number | null;
  source_file: string | null;
}

export interface Fact {
  fact_id: string;
  key: string;
  // Value shape depends on the ontology key (*_paise → integer paise, [] → list,
  // otherwise string). Kept as unknown so consumers narrow at use.
  value: unknown;
  provenance: Provenance;
  confidence: number;
  confirmed: boolean;
  supplied_by: Role;
  created_at: string; // ISO 8601 (datetime is serialised by Pydantic)
}

/**
 * GET /api/facts returns the whole append-only history — every version,
 * confirmed or not, superseded or not (see backend/app/facts.py's
 * FactStore.all_facts). Resuming a form (the wizard, or anything else that
 * wants "what's the current answer for this key") needs the single LIVE
 * fact per key, the same "not in anyone's supersedes" resolution the
 * backend already does in FactStore.confirmed_by_key/all_confirmed — done
 * here client-side since GET /api/facts intentionally returns the raw
 * history, not a resolved view.
 *
 * If more than one live fact shares a key (e.g. a wizard answer and an
 * uploaded document value that were never reconciled — see the planted
 * demo contradiction), a wizard-sourced one wins, since that's what the
 * wizard itself would have last written; otherwise the most recently
 * created live fact for that key is used. Never throws on an empty or
 * malformed list.
 */
export function latestFactsByKey(facts: Fact[]): Map<string, Fact> {
  const superseded = new Set(
    facts.map((f) => f.provenance.supersedes).filter((id): id is string => id !== null),
  );
  const live = facts.filter((f) => !superseded.has(f.fact_id));

  const byKey = new Map<string, Fact[]>();
  for (const fact of live) {
    const bucket = byKey.get(fact.key);
    if (bucket) bucket.push(fact);
    else byKey.set(fact.key, [fact]);
  }

  const result = new Map<string, Fact>();
  for (const [key, candidates] of byKey) {
    const wizardOne = candidates.find((f) => f.provenance.kind === "wizard");
    const chosen =
      wizardOne ??
      candidates.slice().sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    result.set(key, chosen);
  }
  return result;
}

// --------------------------------------------------------------------------
// Uploads / extraction (backend/app/intake/uploads.py)
// --------------------------------------------------------------------------

export interface ExtractionProposal {
  fact_key: string;
  // int = INR paise for *_paise keys; string otherwise (never floats).
  value: string | number;
  source_file: string;
  page: number;
  snippet: string;
  confidence: number;
  // Links to the archived original — see Provenance.document_id above.
  document_id: string | null;
}

// --------------------------------------------------------------------------
// Eligibility (backend/app/eligibility.py)
// --------------------------------------------------------------------------

export interface EligibilityInput {
  post_issue_paid_up_capital_paise: number;
  operating_profit_years: number;
  min_operating_profit_paise: number;
  is_debarred_by_sebi: boolean;
  promoter_director_of_debarred_company: boolean;
  is_wilful_defaulter_or_fraudulent_borrower: boolean;
  is_fugitive_economic_offender: boolean;
  has_outstanding_convertibles: boolean;
  promoter_change_within_1yr: boolean;
  ofs_pct_of_issue: number;
  promoter_shares_demat: boolean;
  partly_paid_shares_outstanding: boolean;
}

export interface ReadinessItem {
  criterion: string;
  clause_ref: string;
  current_state: string;
  fix: string;
  indicative_timeline: string;
}

export interface EligibilityReport {
  result: GateResult;
  items: ReadinessItem[];
}

// --------------------------------------------------------------------------
// Wizard (backend/app/intake/wizard.py)
// --------------------------------------------------------------------------

export interface WizardQuestion {
  fact_key: string;
  section: string;
  prompt: string;
  why_we_ask: string;
  clause_ref: string;
  checklist_entry_id: string;
  input_hint: string; // "money" | "list" | "date" | "text"
  help_text: string | null;
}

// --------------------------------------------------------------------------
// Generation (backend/app/generate/sections.py)
// --------------------------------------------------------------------------

export interface Citation {
  fact_id: string;
  // Backend serialises tuple as a two-element array [start, end].
  text_span: [number, number];
}

export interface GeneratedSection {
  entry_id: string;
  section: string;
  text: string;
  citations: Citation[];
  missing_facts: string[];
}

// --------------------------------------------------------------------------
// Validation (backend/app/validate/*.py)
// --------------------------------------------------------------------------

export interface Claim {
  section_entry_id: string;
  kind: ClaimKind | string; // backend uses free-form str but only these values ship
  subject: string;
  value: string;
  text_span: [number, number];
}

export interface Contradiction {
  subject: string;
  claims: Claim[];
}

export interface BoilerplateFlag {
  entry_id: string;
  text_span: [number, number];
  reason: string; // "generic filler" | "near-duplicate of reference DRHP"
}

export interface Objection {
  entry_id: string;
  objection: string;
  clause_ref: string | null;
  resolved: boolean;
}

// Iterative examiner (backend/app/validate/iterative_examiner.py) — loops
// examine() + revision until a round raises no objection unseen earlier.
export interface ExaminationRound {
  round_number: number;
  objections: Objection[];
  new_objection_count: number;
  revised_entry_ids: string[];
}

export type IterativeExaminerStopReason =
  | "survived"
  | "no_new_objections"
  | "no_revisable_objections"
  | "max_rounds_reached";

export interface IterativeExaminationReport {
  rounds: ExaminationRound[];
  final_sections: GeneratedSection[];
  final_objections: Objection[];
  survived: boolean;
  stop_reason: IterativeExaminerStopReason;
}

// Objects-of-the-Issue arithmetic check (backend/app/validate/arithmetic.py).
// Deterministic integer arithmetic over confirmed facts — no LLM involved.
export interface ArithmeticFinding {
  kind: ArithmeticFindingKind;
  detail: string; // human sentence; figures pre-formatted in lakh/crore
  expected_paise: number | null;
  actual_paise: number | null;
  severity: Severity; // backend FindingSeverity is the same literal set
  clause_ref: string | null;
}

// --------------------------------------------------------------------------
// Coverage (backend/app/coverage.py)
// --------------------------------------------------------------------------

export interface SectionCoverage {
  section: string;
  covered: number;
  total: number;
  out_of_scope: number;
  not_applicable: number; // conditional entries whose has_* condition is unmet
}

export interface CoverageReport {
  sections: SectionCoverage[];
  // NOTE: ``overall_pct`` is a Pydantic @property on the backend and is NOT
  // serialised in the JSON response. Compute it on the frontend if needed.
}

// Reference-filing benchmark (backend/app/coverage.py, data/reference_drhps/)
export interface ChapterMapping {
  heading: string;
  maps_to: string[];
  status: string; // "encoded" | "out_of_scope_auditor" | "not_encoded"
  note: string | null;
}

export interface ReferenceBenchmark {
  company: string;
  source_url: string;
  filed: string;
  exchange: string;
  framework_evidence: string;
  chapters: ChapterMapping[];
}

export interface BenchmarkSummaryRow {
  company: string;
  filed: string;
  chapters_total: number;
  chapters_encoded: number;
  chapters_out_of_scope_auditor: number;
  chapters_not_encoded: string[];
  in_scope_coverage_pct: number;
}

export interface BenchmarkReport {
  references: ReferenceBenchmark[];
  summary: BenchmarkSummaryRow[];
}

// --------------------------------------------------------------------------
// Litigation (backend/app/intake/litigation.py)
// --------------------------------------------------------------------------

export interface LitigationRecord {
  case_number: string;
  forum: string;
  parties: string;
  nature: string;
  amount_involved_paise: number | null;
  status: string;
}

// --------------------------------------------------------------------------
// Gaps (backend/app/validate/gaps.py)
// --------------------------------------------------------------------------

export interface Gap {
  entry_id: string;
  section: string;
  missing_fact_key: string;
  clause_ref: string;
  routed_to: Role;
  severity: Severity;
}

export interface GapReport {
  gaps: Gap[];
}

// --------------------------------------------------------------------------
// Checklist schema (backend/app/schema/models.py)
// --------------------------------------------------------------------------

export interface ChecklistEntry {
  id: string;
  clause_ref: string;
  section: string;
  title: string;
  description: string;
  applicability: string; // "always" or a named condition
  required_facts: string[];
  responsible_role: Role;
  severity: Severity;
  output_targets: OutputTarget[];
  stub: boolean;
}

export interface ChecklistHeader {
  regulation: string;
  amended_through: string; // ISO date
  schema_version: string;
  reviewed_by_human: boolean;
}

export interface Checklist {
  header: ChecklistHeader;
  entries: ChecklistEntry[];
}

// --------------------------------------------------------------------------
// Banker review workflow (backend/app/review/workflow.py)
// --------------------------------------------------------------------------

export interface BankerEdit {
  entry_id: string;
  editor: string;
  before: string;
  after: string;
  at: string; // ISO 8601 datetime
}

export interface ReviewState {
  states: Record<string, SectionState>; // entry_id → state
  audit_trail: BankerEdit[];
}

export interface ExportResponse {
  drhp: string;      // download path, e.g. "/api/assemble/drhp"
  abridged: string;
}

// Extraction reliability (backend/app/extraction_reliability) — mirrors
// ExtractionReliabilityBucket/Report exactly.
export interface ExtractionReliabilityBucket {
  provenance_kind: string; // "document" | "lookup" | "role_upload"
  confidence_band: string;
  total_facts: number;
  corrected_count: number;
  correction_rate: number | null;
  banker_caught_count: number;
}

export interface ExtractionReliabilityReport {
  buckets: ExtractionReliabilityBucket[];
  total_extracted_facts: number;
  total_corrections: number;
  total_banker_caught_corrections: number;
}

// --------------------------------------------------------------------------
// Typed API functions — one per endpoint in backend/app/main.py
// --------------------------------------------------------------------------

// Schema / health
export const getSchema = (): Promise<Checklist> => apiGet<Checklist>("/api/schema");

// Eligibility
export const postEligibility = (input: EligibilityInput): Promise<EligibilityReport> =>
  apiPost<EligibilityReport>("/api/eligibility", input);

// Wizard
export const getWizardQuestions = (lang: string = "en"): Promise<WizardQuestion[]> =>
  apiGet<WizardQuestion[]>(`/api/wizard/questions?lang=${encodeURIComponent(lang)}`);

// Facts
export const getFacts = (): Promise<Fact[]> => apiGet<Fact[]>("/api/facts");

export const postFact = (fact: Fact): Promise<Fact> => apiPost<Fact>("/api/facts", fact);

export const confirmFact = (id: string): Promise<Fact> =>
  apiPost<Fact>(`/api/facts/${encodeURIComponent(id)}/confirm`, {});

export const correctFact = (
  id: string,
  value: unknown,
  provenance: Provenance,
): Promise<Fact> =>
  apiPost<Fact>(`/api/facts/${encodeURIComponent(id)}/correct`, { value, provenance });

// Uploads / proposals
export const uploadExtract = (file: File): Promise<ExtractionProposal[]> => {
  const form = new FormData();
  form.append("file", file);
  return apiUpload<ExtractionProposal[]>("/api/uploads/extract", form);
};

// Inline document viewer (backend/app/intake/vault, app.main's
// GET /api/uploads/{document_id}[/page/{page}]) — the fact-confirmation
// trust mechanic: show the real source, not just a quoted snippet.

/** The archived original's own bytes — used directly for an image upload
 * (<img> via a blob URL, see apiGetBlobUrl) or fetched as text for a .txt
 * upload (see apiGetText). Never used directly for a PDF — see
 * documentPageImagePath below, which renders one page as a PNG instead,
 * since a browser can't display "page N of this PDF" from raw bytes alone
 * without a much heavier client-side PDF library. */
export const uploadRawPath = (documentId: string): string =>
  `/api/uploads/${encodeURIComponent(documentId)}`;

/** One PDF page, rendered as a PNG with ``snippet`` highlighted where the
 * page's own embedded text layer contains it (never for an OCR'd/scanned
 * page — see render_pdf_page_with_highlight's docstring for why that's an
 * honest limitation, not a bug). ``page`` is 1-indexed. */
export const documentPageImagePath = (
  documentId: string,
  page: number,
  snippet?: string,
): string => {
  const query = snippet ? `?snippet=${encodeURIComponent(snippet)}` : "";
  return `/api/uploads/${encodeURIComponent(documentId)}/page/${page}${query}`;
};

export const acceptProposal = (p: ExtractionProposal, role: Role = "promoter"): Promise<Fact> =>
  apiPost<Fact>(`/api/proposals/accept?role=${role}`, p);

// Litigation
export const getLitigation = (entity: string): Promise<LitigationRecord[]> =>
  apiGet<LitigationRecord[]>(`/api/litigation?entity=${encodeURIComponent(entity)}`);

// Generation + sections cache
export const postGenerate = (): Promise<GeneratedSection[]> =>
  apiPost<GeneratedSection[]>("/api/generate", {});

export const getSections = (): Promise<GeneratedSection[]> =>
  apiGet<GeneratedSection[]>("/api/sections");

// Validation
export const getContradictions = (): Promise<Contradiction[]> =>
  apiGet<Contradiction[]>("/api/validate/contradictions");

// Free-prose cross-section consistency (LLM enrichment; [] offline)
export const getSemantic = (): Promise<Contradiction[]> =>
  apiGet<Contradiction[]>("/api/validate/semantic");

export const getBoilerplate = (): Promise<BoilerplateFlag[]> =>
  apiGet<BoilerplateFlag[]>("/api/validate/boilerplate");

export const getArithmetic = (): Promise<ArithmeticFinding[]> =>
  apiGet<ArithmeticFinding[]>("/api/validate/arithmetic");

export const getExaminer = (): Promise<Objection[]> =>
  apiGet<Objection[]>("/api/validate/examiner");

// Promoter-only (same restriction as postGenerate — it can rewrite draft
// text). Requires postGenerate to have run first (backend answers 409
// otherwise). Caches the (possibly revised) final sections the same way
// postGenerate does, so a subsequent getSections() reflects the result.
export const postExaminerIterative = (maxRounds = 3): Promise<IterativeExaminationReport> =>
  apiPost<IterativeExaminationReport>(
    `/api/validate/examiner/iterative?max_rounds=${maxRounds}`,
    {},
  );

// Coverage
export const getCoverage = (): Promise<CoverageReport> =>
  apiGet<CoverageReport>("/api/coverage");

export const getCoverageBenchmark = (): Promise<BenchmarkReport> =>
  apiGet<BenchmarkReport>("/api/coverage/benchmark");

// Alias for the same endpoint — kept so pages can import the shorter name
// without breaking existing getCoverageBenchmark call sites.
export const getBenchmark: () => Promise<BenchmarkReport> = getCoverageBenchmark;

// Gaps
export const getGaps = (): Promise<GapReport> => apiGet<GapReport>("/api/gaps");

// Banker review workflow
export const getReviewState = (): Promise<ReviewState> =>
  apiGet<ReviewState>("/api/review/state");

export const advanceSection = (entryId: string, to: SectionState): Promise<ReviewState> =>
  apiPost<ReviewState>(`/api/review/${encodeURIComponent(entryId)}/advance`, { to });

export const recordEdit = (edit: BankerEdit): Promise<ReviewState> =>
  apiPost<ReviewState>("/api/review/edit", edit);

export const exportPackage = (): Promise<ExportResponse> =>
  apiPost<ExportResponse>("/api/review/export", {});

// Extraction reliability (banker-only)
export const getExtractionReliability = (): Promise<ExtractionReliabilityReport> =>
  apiGet<ExtractionReliabilityReport>("/api/extraction-reliability");

// Regulatory staleness watcher (banker-only, backend/app/regulatory_watch.py)
// — compares the checklist schema's pinned amended_through date against
// SEBI's real, live ICDR-tagged postings. A real external HTTP call, so the
// POST is only ever fired by an explicit banker click, never on page load;
// GET status just reads back whatever the last POST cached (or null if
// nothing has been checked yet this process's lifetime).
export interface RegulatoryUpdate {
  title: string;
  published: string; // ISO date
  url: string;
}

export interface StalenessCheckResult {
  checked_at: string; // ISO datetime
  pinned_amended_through: string; // ISO date
  checked_successfully: boolean;
  newer_updates: RegulatoryUpdate[];
  source: string;
}

export const getRegulatoryWatchStatus = (): Promise<StalenessCheckResult | null> =>
  apiGet<StalenessCheckResult | null>("/api/regulatory-watch/status");

export const postRegulatoryWatchCheck = (): Promise<StalenessCheckResult> =>
  apiPost<StalenessCheckResult>("/api/regulatory-watch/check", {});

// Assembly — returns the URL the browser can point an <a href> / download at.
// Not fetched here because the response is a .docx binary, not JSON.
export const assembleUrl = (target: OutputTarget): string =>
  `/api/assemble/${encodeURIComponent(target)}`;

// Exchange-ready bundle (ZIP: both .docx targets + the full audit trail).
// Like assembleUrl, a plain <a href> download target — the response is binary.
export const bundleUrl = (): string => "/api/export/bundle";

/**
 * Pre-flight the bundle download. GET /api/export/bundle is gated by the
 * certification lock and answers 409 until every blocker-severity section is
 * certified — a bare <a> click would surface that as a broken download, so
 * call this first and show the thrown error instead. Uses GET (FastAPI routes
 * do not answer HEAD) and cancels the body stream immediately so the ZIP is
 * never actually transferred; throws the same "METHOD path → status" Error
 * shape as apiGet/apiPost.
 */
export async function exportBundleCheck(): Promise<void> {
  const path = bundleUrl();
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  await res.body?.cancel();
}

// --------------------------------------------------------------------------
// Money formatter (display layer ONLY — money is integer paise everywhere else)
// --------------------------------------------------------------------------

const PAISE_PER_CRORE = 10 ** 9; // 1 crore rupees = 10^7 rupees = 10^9 paise
const PAISE_PER_LAKH = 10 ** 7;  // 1 lakh  rupees = 10^5 rupees = 10^7 paise

/**
 * Format an integer paise amount for display.
 *
 * Rules:
 * - ≥ 1 crore  → "₹X.XX crore" (Indian grouping on the integer part).
 * - ≥ 1 lakh   → "₹X.XX lakh".
 * - otherwise  → "₹X" or "₹X.YY" if there are leftover paise.
 * - Integer-only rounding (half-up); never floats — the input is an integer,
 *   and the two-decimal fraction is computed via integer arithmetic to match
 *   the backend's ``format_inr_paise`` behaviour.
 * - Non-integer or non-finite input is treated as an error and returned as
 *   ``"₹—"`` rather than lying with a rounded value.
 */
export function formatPaise(paise: number): string {
  if (!Number.isFinite(paise) || !Number.isInteger(paise)) {
    return "₹—";
  }
  const sign = paise < 0 ? "-" : "";
  const magnitude = Math.abs(paise);
  if (magnitude >= PAISE_PER_CRORE) {
    return `${sign}₹${twoDecimals(magnitude, PAISE_PER_CRORE)} crore`;
  }
  if (magnitude >= PAISE_PER_LAKH) {
    return `${sign}₹${twoDecimals(magnitude, PAISE_PER_LAKH)} lakh`;
  }
  const rupees = Math.trunc(magnitude / 100);
  const remainder = magnitude % 100;
  if (remainder) {
    return `${sign}₹${indianGroup(rupees)}.${String(remainder).padStart(2, "0")}`;
  }
  return `${sign}₹${indianGroup(rupees)}`;
}

/** Integer-only half-up rounding of ``magnitude / unit`` to two decimals. */
function twoDecimals(magnitude: number, unit: number): string {
  // hundredths = round(magnitude * 100 / unit); integer arithmetic only.
  const hundredths = Math.floor((magnitude * 100 + Math.floor(unit / 2)) / unit);
  const whole = Math.floor(hundredths / 100);
  const frac = hundredths % 100;
  return `${indianGroup(whole)}.${String(frac).padStart(2, "0")}`;
}

/**
 * Indian digit grouping: last three digits, then every two thereafter.
 * ``1234567`` → ``"12,34,567"``. Works on non-negative integers only.
 */
function indianGroup(n: number): string {
  const s = String(n);
  if (s.length <= 3) return s;
  const head = s.slice(0, -3);
  const tail = s.slice(-3);
  const grouped = head.replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${grouped},${tail}`;
}
