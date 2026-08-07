import { useState } from "react";
import {
  acceptProposal,
  confirmFact,
  formatPaise,
  uploadExtract,
  type ExtractionProposal,
} from "../api/client";
import DocumentSnippetViewer from "../components/DocumentSnippetViewer";

/**
 * The auditor's workspace.
 *
 * The auditor role has always been real server-side — it can upload, extract,
 * accept and confirm facts (see the `require_roles(..., Role.AUDITOR, ...)`
 * endpoints in app.main) — but had no screen of its own, so an auditor signed
 * in and could only read the draft. The one thing CLAUDE.md says *only* an
 * auditor may lawfully supply, restated financial statements, had no way in
 * short of calling the API by hand.
 *
 * This is deliberately narrow: ingest a document, confirm what was extracted
 * from it. The tool formats and cites restated financials; it never generates
 * them, and nothing here changes that.
 */

type Stage = "pending" | "saving" | "confirmed" | "skipped" | "error";

interface ProposalState {
  stage: Stage;
  error?: string;
}

function displayValue(p: ExtractionProposal): string {
  // *_paise keys are integer paise by convention everywhere in this app;
  // formatting is display-layer only (see client.ts formatPaise).
  if (p.fact_key.endsWith("_paise") && typeof p.value === "number") {
    return formatPaise(p.value);
  }
  return String(p.value);
}

export default function AuditorWorkspace() {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [proposals, setProposals] = useState<ExtractionProposal[]>([]);
  const [states, setStates] = useState<Record<number, ProposalState>>({});

  async function handleUpload(file: File) {
    setUploading(true);
    setUploadError(null);
    setProposals([]);
    setStates({});
    setFileName(file.name);
    try {
      setProposals(await uploadExtract(file));
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleConfirm(idx: number, proposal: ExtractionProposal) {
    setStates((prev) => ({ ...prev, [idx]: { stage: "saving" } }));
    try {
      // supplied_by is forced server-side from the authenticated role, so
      // passing "auditor" here only keeps the client honest — the server
      // does not trust it (see app.main's add_fact).
      const fact = await acceptProposal(proposal, "auditor");
      await confirmFact(fact.fact_id);
      setStates((prev) => ({ ...prev, [idx]: { stage: "confirmed" } }));
    } catch (err) {
      setStates((prev) => ({
        ...prev,
        [idx]: { stage: "error", error: err instanceof Error ? err.message : "Failed" },
      }));
    }
  }

  const confirmedCount = Object.values(states).filter((s) => s.stage === "confirmed").length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Auditor Workspace</h1>
        <p className="mt-1 text-sm text-gray-600 max-w-3xl">
          Upload the statements you have prepared — restated financials, audit
          reports, schedules — and confirm each value the tool reads out of
          them. Confirming is you vouching for the figure; nothing enters the
          draft until you do.
        </p>
        <p className="mt-2 text-sm text-gray-600 max-w-3xl">
          The tool ingests, formats and cites this content. It does not, and
          will not, generate restated financial statements — that is your work
          by law, and the coverage score marks it out of scope rather than
          quietly counting it as done.
        </p>
      </div>

      <div className="rounded border border-slate-200 bg-white p-4">
        <h2 className="text-lg font-semibold mb-2">Upload a document</h2>
        <label className="inline-flex items-center gap-3 cursor-pointer">
          <span className="rounded bg-blue-600 text-white text-sm px-3 py-1.5 hover:bg-blue-700">
            Choose file
          </span>
          <input
            type="file"
            className="hidden"
            disabled={uploading}
            accept=".pdf,.txt,.png,.jpg,.jpeg,.tiff,.tif,.bmp,.webp"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleUpload(file);
              e.target.value = "";
            }}
          />
          {uploading && <span className="text-sm text-slate-500">Extracting…</span>}
          {fileName && !uploading && (
            <span className="text-sm text-slate-600 break-all">{fileName}</span>
          )}
        </label>
        <p className="mt-2 text-xs text-gray-500">
          PDF, image or text. A scanned page with no text layer goes through
          OCR, and anything read that way is scored lower on purpose so it gets
          extra scrutiny.
        </p>
        {uploadError && (
          <p className="mt-2 text-sm text-red-700" role="alert">
            {uploadError}
          </p>
        )}
      </div>

      {proposals.length > 0 && (
        <div className="rounded border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
            <h2 className="text-lg font-semibold">
              Extracted values <span className="text-gray-500 font-normal">({proposals.length})</span>
            </h2>
            <span className="text-sm text-gray-600" aria-live="polite">
              {confirmedCount} of {proposals.length} confirmed
            </span>
          </div>

          <ul className="space-y-3">
            {proposals.map((p, idx) => {
              const state = states[idx] ?? { stage: "pending" as Stage };
              return (
                <li key={`${p.fact_key}-${idx}`} className="rounded border border-gray-200 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-gray-500 break-all">{p.fact_key}</span>
                    {p.confidence < 0.7 && (
                      <span className="rounded bg-amber-100 text-amber-800 border border-amber-300 px-1.5 py-0.5 text-[11px]">
                        low confidence {Math.round(p.confidence * 100)}%
                      </span>
                    )}
                  </div>
                  <div className="mt-1 text-gray-900 font-medium break-words">
                    {displayValue(p)}
                  </div>

                  {p.snippet && (
                    <blockquote className="mt-1.5 border-l-2 border-gray-200 pl-2 text-xs text-gray-600 break-words">
                      {p.snippet}
                    </blockquote>
                  )}

                  <DocumentSnippetViewer
                    documentId={p.document_id ?? null}
                    page={p.page ?? null}
                    sourceFile={p.source_file ?? null}
                    snippet={p.snippet ?? null}
                  />

                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {state.stage === "confirmed" ? (
                      <span className="text-sm text-emerald-700">Confirmed</span>
                    ) : state.stage === "skipped" ? (
                      <span className="text-sm text-gray-500">Skipped</span>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => void handleConfirm(idx, p)}
                          disabled={state.stage === "saving"}
                          className="rounded bg-gray-900 px-3 py-1.5 text-xs text-white hover:bg-gray-700 disabled:opacity-50"
                        >
                          {state.stage === "saving" ? "Saving…" : "Confirm this value"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setStates((prev) => ({ ...prev, [idx]: { stage: "skipped" } }))}
                          className="rounded border border-gray-300 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50"
                        >
                          Skip
                        </button>
                      </>
                    )}
                    {state.stage === "error" && (
                      <span className="text-sm text-red-700" role="alert">
                        {state.error}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {proposals.length === 0 && fileName && !uploading && !uploadError && (
        <p className="text-sm text-gray-600">
          Nothing could be read from that document. If it is a scan, OCR may
          have found no usable text — the draft is unchanged either way.
        </p>
      )}
    </div>
  );
}
