import { describe, expect, it } from "vitest";
import { formatPaise, latestFactsByKey } from "./client";
import type { Fact } from "./client";

/**
 * The two pieces of real logic on the frontend side.
 *
 * Everything else in `client.ts` is a typed fetch wrapper — a bug there
 * surfaces immediately as a broken screen. These two are different: both
 * fail *silently and plausibly*. A money formatter off by a factor of a
 * hundred still renders a believable number, and a supersedes-resolution bug
 * shows a stale-but-real answer. Neither would look wrong on a demo.
 *
 * No jsdom, no component rendering: `client.ts` touches localStorage and
 * window only inside functions these tests never call, so this runs in plain
 * node.
 */

// --------------------------------------------------------------------------
// formatPaise — display layer only; money is integer paise everywhere else
// --------------------------------------------------------------------------

describe("formatPaise", () => {
  it("renders crore and lakh at the Indian thresholds", () => {
    expect(formatPaise(14 * 10 ** 9)).toBe("₹14.00 crore");
    expect(formatPaise(10 ** 9)).toBe("₹1.00 crore"); // exactly 1 crore
    expect(formatPaise(10 ** 7)).toBe("₹1.00 lakh"); // exactly 1 lakh
    expect(formatPaise(10 ** 7 - 1)).toBe("₹99,999.99"); // one paisa under
  });

  it("picks the unit before rounding, so the boundary reads as 100 lakh", () => {
    // One paisa under a crore: the unit is chosen first (still lakh), then
    // the value rounds half-up to 100.00 — so this is "₹100.00 lakh", not
    // "₹1.00 crore" and not "₹99.99 lakh". Pinned because
    // app.assemble.docx_builder.format_inr_paise does exactly the same
    // thing, and the on-screen figure and the figure in the assembled
    // .docx disagreeing would be its own kind of contradiction.
    expect(formatPaise(10 ** 9 - 1)).toBe("₹100.00 lakh");
  });

  it("uses Indian digit grouping below a lakh", () => {
    // Last three digits, then groups of two — not the western 3-3-3.
    expect(formatPaise(123456700)).toBe("₹12.35 lakh");
    expect(formatPaise(99_99_999)).toBe("₹99,999.99");
    expect(formatPaise(100_00)).toBe("₹100");
  });

  it("shows paise only when there are leftover paise", () => {
    expect(formatPaise(50_000)).toBe("₹500");
    expect(formatPaise(50_050)).toBe("₹500.50");
    expect(formatPaise(50_005)).toBe("₹500.05"); // zero-padded, not "₹500.5"
  });

  it("keeps the sign outside the rupee symbol", () => {
    expect(formatPaise(-14 * 10 ** 9)).toBe("-₹14.00 crore");
    expect(formatPaise(-50_050)).toBe("-₹500.50");
  });

  it("refuses to render a value it cannot represent exactly", () => {
    // A float or NaN reaching here means paise arithmetic went wrong
    // upstream. Rendering a rounded guess would hide that; "₹—" does not.
    expect(formatPaise(1234.5)).toBe("₹—");
    expect(formatPaise(NaN)).toBe("₹—");
    expect(formatPaise(Infinity)).toBe("₹—");
  });

  it("handles zero", () => {
    expect(formatPaise(0)).toBe("₹0");
  });
});

// --------------------------------------------------------------------------
// latestFactsByKey — client-side mirror of FactStore.confirmed_by_key's
// supersedes exclusion. GET /api/facts deliberately returns the raw
// append-only history, so the resolution has to happen here.
// --------------------------------------------------------------------------

function fact(
  id: string,
  key: string,
  value: string | number,
  opts: { supersedes?: string | null; kind?: string; createdAt?: string } = {},
): Fact {
  return {
    fact_id: id,
    key,
    value,
    provenance: {
      kind: (opts.kind ?? "document") as Fact["provenance"]["kind"],
      detail: "test",
      snippet: null,
      supersedes: opts.supersedes ?? null,
      document_id: null,
      page: null,
      source_file: null,
    },
    confidence: 0.9,
    confirmed: true,
    supplied_by: "promoter",
    corrected_by_role: null,
    created_at: opts.createdAt ?? "2026-08-01T00:00:00+00:00",
  } as Fact;
}

describe("latestFactsByKey", () => {
  it("excludes a fact that a later correction superseded", () => {
    const resolved = latestFactsByKey([
      fact("v1", "issue_size_paise", 1_000),
      fact("v2", "issue_size_paise", 2_000, { supersedes: "v1" }),
    ]);
    expect(resolved.get("issue_size_paise")?.fact_id).toBe("v2");
  });

  it("follows a multi-step correction chain to the live value", () => {
    const resolved = latestFactsByKey([
      fact("v1", "issue_size_paise", 1_000),
      fact("v2", "issue_size_paise", 2_000, { supersedes: "v1" }),
      fact("v3", "issue_size_paise", 3_000, { supersedes: "v2" }),
    ]);
    expect(resolved.get("issue_size_paise")?.value).toBe(3_000);
  });

  it("prefers a wizard answer over a document extraction for the same key", () => {
    // The promoter typing an answer outranks an extraction, regardless of
    // which arrived later — this is what makes the wizard rehydrate to what
    // the promoter actually entered.
    const resolved = latestFactsByKey([
      fact("doc", "issuer_name", "Sunrise Agro", {
        kind: "document",
        createdAt: "2026-08-02T00:00:00+00:00",
      }),
      fact("wiz", "issuer_name", "Sunrise Agrotech Ltd", {
        kind: "wizard",
        createdAt: "2026-08-01T00:00:00+00:00",
      }),
    ]);
    expect(resolved.get("issuer_name")?.fact_id).toBe("wiz");
  });

  it("falls back to the newest candidate when none came from the wizard", () => {
    const resolved = latestFactsByKey([
      fact("old", "issuer_name", "Old", { createdAt: "2026-08-01T00:00:00+00:00" }),
      fact("new", "issuer_name", "New", { createdAt: "2026-08-03T00:00:00+00:00" }),
    ]);
    expect(resolved.get("issuer_name")?.fact_id).toBe("new");
  });

  it("keeps unrelated keys independent", () => {
    const resolved = latestFactsByKey([
      fact("a", "issuer_name", "Sunrise"),
      fact("b", "issue_size_paise", 1_000),
    ]);
    expect(resolved.size).toBe(2);
  });

  it("returns an empty map for no facts", () => {
    expect(latestFactsByKey([]).size).toBe(0);
  });
});
