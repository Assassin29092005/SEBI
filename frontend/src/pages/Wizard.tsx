export default function Wizard() {
  // TODO (day 3–4): render questions from GET /api/wizard/questions.
  // Every question shows "why we ask this" with its clause_ref.
  // Includes the fact-confirmation screens: extracted values are proposals
  // until confirmed against the highlighted source snippet.
  return (
    <section>
      <h1 className="text-2xl font-semibold mb-2">Guided Wizard</h1>
      <p className="text-gray-600">
        Tell us about your business, or upload documents you already have —
        we’ll extract the details and ask you to confirm each one.
      </p>
    </section>
  );
}
