# Demo Company — Sunrise Agrotech Ltd

The synthetic demo SME. **Everything here is fake**: CIN/PAN-format
identifiers are deliberately invalid, and no real business data, financials,
or personal identifiers may ever be added.

Fake identifiers pinned across every fixture:

- Name: `Sunrise Agrotech Ltd`
- CIN:  `U01100MH2015PLC000000`
- PAN:  `AAACS0000A`

## Fixtures on disk

- `wizard_answers.json` — filled promoter wizard answers. One entry per
  promoter-owned, non-stub `required_facts` key defined by the current
  checklist (`app.schema.loader.load_checklist()`). `*_paise` keys carry
  integer paise; `[]` keys carry JSON arrays.
- `uploads/` — fake documents for the extraction demo. Each file is plain
  text with a mix of prose and `Label: value` lines that follow the shared
  label convention in `app.intake.uploads.label_for_key` (strip trailing
  `[]` and `_paise`, replace `_` with spaces, Title Case).
  - `bank_sanction_letter.txt` — indicative term sheet from the mock lead
    manager.
  - `certificate_of_incorporation.txt` — MCA-style certificate stub.
  - `audited_summary.txt` — extracted highlights from the mock statutory
    auditor.
- `litigation_records.json` — canned records loaded by
  `app.intake.litigation.MockLitigationConnector.search` when the entity
  name contains "sunrise agrotech" (case-insensitive). Three records:
  a civil recovery suit, a GST demand, and a consumer complaint. Amounts
  in paise. Every case number and forum is synthetic.

## The planted contradiction (for the live contradiction-check demo)

`issue_size_paise` disagrees between two sources on purpose so the
validation suite can catch it live on stage:

| Source | File | Value on disk | Meaning |
|---|---|---|---|
| Wizard answer | `wizard_answers.json` | `12500000000` | Rs 12.50 crore |
| Extracted from upload | `uploads/bank_sanction_letter.txt` (`Issue Size: Rs 14.00 crore`) | `14000000000` (after `parse_inr_to_paise`) | Rs 14.00 crore |

The wizard number is what the promoter typed in as the final proposal;
the sanction letter is a stale earlier draft the promoter also uploaded.
The contradiction detector (`app.validate.contradictions.cross_check`)
raises this as a numeric disagreement on the `issue_size_paise` claim.
Everything else in the fixtures is internally consistent.
