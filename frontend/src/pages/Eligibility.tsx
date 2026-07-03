// Eligibility — the demo's opening screen. Promoter-first plain language.
// A failed gate is NOT a rejection: it's a readiness to-do list.
//
// All money on this form is entered in rupees (₹) and converted to integer
// paise before POST — the backend contract is "*_paise" everywhere. Never
// send floats: paise are integers by regulation-domain definition.

import { useState } from "react";
import { Link } from "react-router-dom";

import {
  postEligibility,
  type EligibilityInput,
  type EligibilityReport,
  type ReadinessItem,
} from "../api/client";

// --------------------------------------------------------------------------
// Form state — rupees as strings so the input can be empty while the user
// types. Booleans as booleans. We validate + convert on submit.
// --------------------------------------------------------------------------

interface FormState {
  post_issue_paid_up_capital_rupees: string;
  operating_profit_years: string;
  min_operating_profit_rupees: string;
  ofs_pct_of_issue: string;
  is_debarred_by_sebi: boolean;
  promoter_director_of_debarred_company: boolean;
  is_wilful_defaulter_or_fraudulent_borrower: boolean;
  is_fugitive_economic_offender: boolean;
  has_outstanding_convertibles: boolean;
  promoter_change_within_1yr: boolean;
  promoter_shares_demat: boolean;
  partly_paid_shares_outstanding: boolean;
}

const INITIAL: FormState = {
  post_issue_paid_up_capital_rupees: "",
  operating_profit_years: "",
  min_operating_profit_rupees: "",
  ofs_pct_of_issue: "",
  is_debarred_by_sebi: false,
  promoter_director_of_debarred_company: false,
  is_wilful_defaulter_or_fraudulent_borrower: false,
  is_fugitive_economic_offender: false,
  has_outstanding_convertibles: false,
  promoter_change_within_1yr: false,
  // Default the two "should be true to pass" flags to true — a healthy issuer
  // has demat promoter holdings and no partly-paid shares. Promoter can flip.
  promoter_shares_demat: true,
  partly_paid_shares_outstanding: false,
};

// Yes/no toggles for the disqualification questions. `pass_when` records the
// answer that keeps the gate open, so we can hint it in the UI without ever
// blocking the promoter from telling the truth.
interface BoolField {
  key: keyof FormState;
  label: string;
  help: string;
  pass_when: boolean;
}

const BOOL_FIELDS: BoolField[] = [
  {
    key: "is_debarred_by_sebi",
    label: "Has the company (or any promoter/director) been debarred by SEBI?",
    help: "Any subsisting SEBI debarment blocks a public issue.",
    pass_when: false,
  },
  {
    key: "promoter_director_of_debarred_company",
    label:
      "Is any promoter or director currently a promoter/director of a company debarred by SEBI?",
    help: "Association with a debarred entity is itself a disqualification.",
    pass_when: false,
  },
  {
    key: "is_wilful_defaulter_or_fraudulent_borrower",
    label: "Has the company or any promoter been classified as a wilful defaulter or fraudulent borrower?",
    help: "A wilful-defaulter tag from any bank/FI is a hard block.",
    pass_when: false,
  },
  {
    key: "is_fugitive_economic_offender",
    label: "Is any promoter or director a declared fugitive economic offender?",
    help: "Under the Fugitive Economic Offenders Act, 2018.",
    pass_when: false,
  },
  {
    key: "has_outstanding_convertibles",
    label: "Are there any outstanding convertible securities or rights to receive equity shares?",
    help: "Options granted to employees under an approved ESOP are the usual exception; other overhang must be cleared before the DRHP.",
    pass_when: false,
  },
  {
    key: "promoter_change_within_1yr",
    label: "Has there been a change of promoters in the last one year?",
    help: "ICDR requires promoter stability in the twelve months preceding the filing.",
    pass_when: false,
  },
  {
    key: "promoter_shares_demat",
    label: "Are all promoter shares held in dematerialised form?",
    help: "Mandatory before filing — physical certificates must be dematerialised.",
    pass_when: true,
  },
  {
    key: "partly_paid_shares_outstanding",
    label: "Are any partly paid-up equity shares still outstanding?",
    help: "All equity shares must be fully paid-up by the filing date.",
    pass_when: false,
  },
];

// --------------------------------------------------------------------------
// Rupee → paise conversion. Integer math only.
// Accepts "12,34,567" or "12,34,567.50"; returns paise as an integer.
// Throws if the value is not a valid non-negative amount.
// --------------------------------------------------------------------------

function rupeesToPaise(raw: string): number {
  const cleaned = raw.replace(/[,\s₹]/g, "");
  if (!cleaned) throw new Error("Please enter an amount in rupees.");
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) {
    throw new Error("Enter a whole rupee amount, or up to two decimal places for paise.");
  }
  const [wholeStr, fracStr = ""] = cleaned.split(".");
  const whole = Number.parseInt(wholeStr, 10);
  const frac = Number.parseInt((fracStr + "00").slice(0, 2), 10) || 0;
  if (!Number.isFinite(whole) || whole < 0) {
    throw new Error("Amount cannot be negative.");
  }
  return whole * 100 + frac;
}

function parseIntStrict(raw: string, field: string): number {
  const cleaned = raw.trim();
  if (!cleaned) throw new Error(`Please enter ${field}.`);
  if (!/^\d+$/.test(cleaned)) {
    throw new Error(`${field} must be a whole number.`);
  }
  return Number.parseInt(cleaned, 10);
}

function parsePercent(raw: string): number {
  const cleaned = raw.trim();
  if (!cleaned) throw new Error("Please enter the OFS percentage of the issue.");
  if (!/^\d+(\.\d+)?$/.test(cleaned)) {
    throw new Error("OFS percentage must be a number between 0 and 100.");
  }
  const n = Number.parseFloat(cleaned);
  if (n < 0 || n > 100) {
    throw new Error("OFS percentage must be between 0 and 100.");
  }
  return n;
}

function buildInput(form: FormState): EligibilityInput {
  return {
    post_issue_paid_up_capital_paise: rupeesToPaise(form.post_issue_paid_up_capital_rupees),
    operating_profit_years: parseIntStrict(form.operating_profit_years, "years of operating profit"),
    min_operating_profit_paise: rupeesToPaise(form.min_operating_profit_rupees),
    ofs_pct_of_issue: parsePercent(form.ofs_pct_of_issue),
    is_debarred_by_sebi: form.is_debarred_by_sebi,
    promoter_director_of_debarred_company: form.promoter_director_of_debarred_company,
    is_wilful_defaulter_or_fraudulent_borrower: form.is_wilful_defaulter_or_fraudulent_borrower,
    is_fugitive_economic_offender: form.is_fugitive_economic_offender,
    has_outstanding_convertibles: form.has_outstanding_convertibles,
    promoter_change_within_1yr: form.promoter_change_within_1yr,
    promoter_shares_demat: form.promoter_shares_demat,
    partly_paid_shares_outstanding: form.partly_paid_shares_outstanding,
  };
}

// --------------------------------------------------------------------------
// Small presentational helpers — kept local so this page stays a single file.
// --------------------------------------------------------------------------

function FieldLabel({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-gray-800">{children}</span>
      {hint ? <span className="mt-0.5 block text-xs text-gray-500">{hint}</span> : null}
    </label>
  );
}

function BoolRow({
  field,
  value,
  onChange,
}: {
  field: BoolField;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  const risky = value !== field.pass_when;
  return (
    <div
      className={`rounded-md border p-3 ${
        risky ? "border-amber-300 bg-amber-50" : "border-gray-200 bg-white"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-gray-800">{field.label}</p>
          <p className="mt-1 text-xs text-gray-500">{field.help}</p>
        </div>
        <div className="flex shrink-0 gap-1" role="radiogroup" aria-label={field.label}>
          <button
            type="button"
            role="radio"
            aria-checked={value === true}
            onClick={() => onChange(true)}
            className={`rounded-md px-3 py-1 text-sm font-medium ${
              value === true
                ? "bg-gray-900 text-white"
                : "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
            }`}
          >
            Yes
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={value === false}
            onClick={() => onChange(false)}
            className={`rounded-md px-3 py-1 text-sm font-medium ${
              value === false
                ? "bg-gray-900 text-white"
                : "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
            }`}
          >
            No
          </button>
        </div>
      </div>
    </div>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-700">
      {children}
    </span>
  );
}

function ReadinessCard({ item }: { item: ReadinessItem }) {
  return (
    <li className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-start justify-between gap-3">
        <h3 className="text-sm font-semibold text-gray-900">{item.criterion}</h3>
        <Chip>{item.clause_ref}</Chip>
      </div>
      <dl className="space-y-2 text-sm">
        <div>
          <dt className="text-xs uppercase tracking-wide text-gray-500">Where you are today</dt>
          <dd className="text-gray-800">{item.current_state}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-gray-500">What to fix</dt>
          <dd className="text-gray-800">{item.fix}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-gray-500">Indicative timeline</dt>
          <dd className="text-gray-800">{item.indicative_timeline}</dd>
        </div>
      </dl>
    </li>
  );
}

// --------------------------------------------------------------------------
// The page.
// --------------------------------------------------------------------------

export default function Eligibility() {
  const [form, setForm] = useState<FormState>(INITIAL);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<EligibilityReport | null>(null);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setReport(null);
    let payload: EligibilityInput;
    try {
      payload = buildInput(form);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Please check the form.");
      return;
    }
    setSubmitting(true);
    try {
      const result = await postEligibility(payload);
      setReport(result);
    } catch (err) {
      setError(
        err instanceof Error
          ? `Could not check eligibility: ${err.message}`
          : "Could not check eligibility. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  function onReset() {
    setForm(INITIAL);
    setReport(null);
    setError(null);
  }

  const passed = report?.result === "pass";

  return (
    <section className="mx-auto max-w-3xl">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-gray-900">Eligibility Check</h1>
        <p className="mt-2 text-gray-600">
          Answer a few questions to check whether your company is ready to file an SME IPO
          under SEBI ICDR Chapter IX. If you're not ready yet, we'll show you exactly what to
          fix and how long it usually takes — this is a to-do list, not a rejection.
        </p>
      </header>

      {report ? (
        <div className="mb-6">
          {passed ? (
            <div className="rounded-lg border border-green-300 bg-green-50 p-5">
              <h2 className="text-lg font-semibold text-green-900">
                Good news — you meet the basic eligibility criteria.
              </h2>
              <p className="mt-1 text-sm text-green-800">
                This is a first-pass screen against Chapter IX. Your merchant banker will
                still perform full due diligence before certification.
              </p>
              <div className="mt-4 flex gap-3">
                <Link
                  to="/wizard"
                  className="inline-flex items-center rounded-md bg-green-700 px-4 py-2 text-sm font-semibold text-white hover:bg-green-800"
                >
                  Continue to the Wizard
                </Link>
                <button
                  type="button"
                  onClick={onReset}
                  className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
                >
                  Start over
                </button>
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-amber-300 bg-amber-50 p-5">
              <h2 className="text-lg font-semibold text-amber-900">
                You're not ready yet — here's the path.
              </h2>
              <p className="mt-1 text-sm text-amber-800">
                Each item below is a specific gap against Chapter IX with a fix and an
                indicative timeline. Work through them with your team; you can come back and
                re-check any time.
              </p>
              {report.items.length === 0 ? (
                <p className="mt-4 text-sm text-amber-900">
                  No specific criteria were returned. Please review your inputs and try again.
                </p>
              ) : (
                <ul className="mt-4 space-y-3">
                  {report.items.map((item, i) => (
                    <ReadinessCard key={`${item.criterion}-${i}`} item={item} />
                  ))}
                </ul>
              )}
              <div className="mt-5">
                <button
                  type="button"
                  onClick={onReset}
                  className="rounded-md border border-amber-400 bg-white px-4 py-2 text-sm font-medium text-amber-900 hover:bg-amber-100"
                >
                  Update my answers
                </button>
              </div>
            </div>
          )}
        </div>
      ) : null}

      {!report ? (
        <form onSubmit={onSubmit} className="space-y-6" noValidate>
          <fieldset className="space-y-4 rounded-lg border border-gray-200 bg-white p-5">
            <legend className="px-1 text-sm font-semibold text-gray-800">
              Company and issue size
            </legend>

            <FieldLabel hint="Total paid-up equity capital after the IPO, in rupees. E.g. 30000000 for ₹3 crore.">
              Post-issue paid-up capital (₹)
              <input
                type="text"
                inputMode="decimal"
                value={form.post_issue_paid_up_capital_rupees}
                onChange={(e) => update("post_issue_paid_up_capital_rupees", e.target.value)}
                placeholder="e.g. 30000000"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
              />
            </FieldLabel>

            <FieldLabel hint="How many of the last three financial years the company posted an operating profit.">
              Years with operating profit (out of last 3)
              <input
                type="number"
                min={0}
                max={3}
                step={1}
                value={form.operating_profit_years}
                onChange={(e) => update("operating_profit_years", e.target.value)}
                placeholder="e.g. 2"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
              />
            </FieldLabel>

            <FieldLabel hint="Lowest annual operating profit in that period, in rupees. Used to check the SME profitability floor.">
              Minimum annual operating profit in those years (₹)
              <input
                type="text"
                inputMode="decimal"
                value={form.min_operating_profit_rupees}
                onChange={(e) => update("min_operating_profit_rupees", e.target.value)}
                placeholder="e.g. 100000"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
              />
            </FieldLabel>

            <FieldLabel hint="Offer for Sale as a share of the total issue size. Capped at 20% for SME IPOs.">
              OFS as % of the issue
              <div className="relative mt-1">
                <input
                  type="number"
                  min={0}
                  max={100}
                  step="0.1"
                  value={form.ofs_pct_of_issue}
                  onChange={(e) => update("ofs_pct_of_issue", e.target.value)}
                  placeholder="e.g. 10"
                  className="block w-full rounded-md border border-gray-300 px-3 py-2 pr-8 text-sm focus:border-gray-500 focus:outline-none"
                />
                <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm text-gray-500">
                  %
                </span>
              </div>
            </FieldLabel>
          </fieldset>

          <fieldset className="space-y-3 rounded-lg border border-gray-200 bg-white p-5">
            <legend className="px-1 text-sm font-semibold text-gray-800">
              Regulatory disqualifications
            </legend>
            <p className="text-xs text-gray-500">
              Answer honestly. A "Yes" or "No" that trips a gate produces a readiness item
              you can work on — nothing here is a permanent block.
            </p>
            <div className="space-y-2">
              {BOOL_FIELDS.map((field) => (
                <BoolRow
                  key={field.key}
                  field={field}
                  value={form[field.key] as boolean}
                  onChange={(v) => update(field.key, v as FormState[typeof field.key])}
                />
              ))}
            </div>
          </fieldset>

          {error ? (
            <div
              role="alert"
              className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800"
            >
              {error}
            </div>
          ) : null}

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={submitting}
              className="rounded-md bg-gray-900 px-5 py-2 text-sm font-semibold text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Checking…" : "Check eligibility"}
            </button>
            <button
              type="button"
              onClick={onReset}
              disabled={submitting}
              className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-60"
            >
              Clear
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
