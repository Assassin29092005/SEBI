export default function Eligibility() {
  // TODO (day 3–4): eligibility form → POST /api/eligibility.
  // A failed gate renders the readiness report (what to fix, timeline) — not a dead end.
  return (
    <section>
      <h1 className="text-2xl font-semibold mb-2">Eligibility Check</h1>
      <p className="text-gray-600">
        Answer a few questions to check your eligibility for an SME IPO under
        SEBI ICDR Chapter IX. If you’re not ready yet, we’ll show you exactly
        what to fix and how long it typically takes.
      </p>
    </section>
  );
}
