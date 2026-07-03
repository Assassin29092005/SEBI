export default function BankerDashboard() {
  // TODO (day 8–9): per-section review states (draft → reviewed → certified),
  // edit audit trail, and the certification lock — export disabled until
  // every blocker-severity section is certified.
  return (
    <section>
      <h1 className="text-2xl font-semibold mb-2">Banker Dashboard</h1>
      <p className="text-gray-600">
        Review and certify each section. The exchange-ready package unlocks
        only when all blocker sections are certified.
      </p>
    </section>
  );
}
