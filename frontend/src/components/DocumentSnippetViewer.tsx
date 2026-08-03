import { useEffect, useState } from "react";
import {
  apiGetBlobUrl,
  apiGetText,
  documentPageImagePath,
  revokeObjectUrl,
  uploadRawPath,
} from "../api/client";

// Matches backend/app/intake/uploads.py's _IMAGE_EXTENSIONS exactly — same
// convention for deciding "is this a standalone image upload".
const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"];

type Kind = "pdf" | "image" | "text";

function kindOf(sourceFile: string): Kind {
  const lower = sourceFile.toLowerCase();
  if (lower.endsWith(".pdf")) return "pdf";
  if (IMAGE_EXTENSIONS.some((ext) => lower.endsWith(ext))) return "image";
  return "text";
}

/** Splits `text` into [before, match, after] around the first occurrence of
 * `snippet`, or returns null if the snippet isn't found verbatim (a
 * mismatch shouldn't happen — the backend only ever proposes a snippet
 * that's a real substring of the source — but a viewer must never crash
 * over it either way). */
function splitOnSnippet(text: string, snippet: string): [string, string, string] | null {
  if (!snippet) return null;
  const idx = text.indexOf(snippet);
  if (idx === -1) return null;
  return [text.slice(0, idx), text.slice(idx, idx + snippet.length), text.slice(idx + snippet.length)];
}

type LoadState = "idle" | "loading" | "ready" | "error";

/**
 * Inline viewer for the document a fact/proposal was extracted from — the
 * fact-confirmation trust mechanic (CLAUDE.md: "confirm... against the
 * highlighted source snippet") made real instead of a bare quoted string.
 *
 * Collapsed by default and fetched lazily on expand, since a page carrying
 * many proposals from one multi-page upload shouldn't eagerly load an image
 * per proposal. Three source kinds, matching the backend's own extension
 * convention:
 *  - PDF: renders one page as a PNG via the backend (a browser can't show
 *    "page N of this PDF" from raw bytes without a much heavier client-side
 *    PDF library), highlighted server-side when the page's own embedded
 *    text layer contains the snippet — never for an OCR'd/scanned page,
 *    which has no such layer; that page still renders, just without the
 *    highlight overlay (an honest limitation, not a bug).
 *  - Image: the archived original is already an image — shown directly.
 *  - Anything else (.txt): the raw text is fetched and the exact snippet
 *    substring is highlighted client-side — the one case where the
 *    highlight is pixel/character-perfect, since there's no rendering step
 *    to lose position information across.
 *
 * Renders nothing when `documentId` is null — happens when archiving
 * failed server-side (best-effort) or for a fact predating this feature;
 * the caller's existing plain snippet quote already covers that case.
 */
export default function DocumentSnippetViewer({
  documentId,
  sourceFile,
  page,
  snippet,
}: {
  documentId: string | null;
  sourceFile: string;
  page: number | null;
  snippet: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [state, setState] = useState<LoadState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    if (!expanded || !documentId) return;
    let cancelled = false;
    const kind = kindOf(sourceFile);
    setState("loading");
    setError(null);

    const load = async () => {
      if (kind === "pdf") {
        const url = await apiGetBlobUrl(
          documentPageImagePath(documentId, page ?? 1, snippet || undefined),
        );
        if (cancelled) {
          revokeObjectUrl(url);
          return;
        }
        setImageUrl(url);
      } else if (kind === "image") {
        const url = await apiGetBlobUrl(uploadRawPath(documentId));
        if (cancelled) {
          revokeObjectUrl(url);
          return;
        }
        setImageUrl(url);
      } else {
        const raw = await apiGetText(uploadRawPath(documentId));
        if (cancelled) return;
        setText(raw);
      }
    };

    load()
      .then(() => {
        if (!cancelled) setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setState("error");
      });

    return () => {
      cancelled = true;
    };
  }, [expanded, documentId, sourceFile, page, snippet]);

  // Revoke the blob URL on unmount / whenever we're about to fetch a new one.
  useEffect(() => {
    return () => {
      if (imageUrl) revokeObjectUrl(imageUrl);
    };
  }, [imageUrl]);

  if (!documentId) return null;

  const kind = kindOf(sourceFile);

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="text-xs text-blue-700 hover:underline font-medium"
      >
        {expanded ? "Hide source document" : "View source document"}
      </button>

      {expanded && (
        <div className="mt-2 rounded border border-gray-200 bg-white p-2">
          {state === "loading" && (
            <p className="text-xs text-gray-500">Loading source document…</p>
          )}
          {state === "error" && (
            <p className="text-xs text-red-700">
              Could not load the source document ({error}). The snippet above is still the
              extracted text.
            </p>
          )}
          {state === "ready" && kind !== "text" && imageUrl && (
            <div>
              <img
                src={imageUrl}
                alt={`${sourceFile}${page ? ` — page ${page}` : ""}`}
                className="max-w-full max-h-[70vh] border border-gray-100"
              />
              {kind === "pdf" && (
                <p className="mt-1 text-xs text-gray-500">
                  Page {page ?? 1} of {sourceFile}
                  {snippet ? " — highlighted where the page's embedded text contains it" : ""}.
                </p>
              )}
            </div>
          )}
          {state === "ready" && kind === "text" && text !== null && (
            <TextWithHighlight text={text} snippet={snippet} sourceFile={sourceFile} />
          )}
        </div>
      )}
    </div>
  );
}

function TextWithHighlight({
  text,
  snippet,
  sourceFile,
}: {
  text: string;
  snippet: string;
  sourceFile: string;
}) {
  const parts = splitOnSnippet(text, snippet);
  return (
    <div>
      <pre className="max-h-[50vh] overflow-y-auto whitespace-pre-wrap break-words text-xs text-gray-800 font-sans">
        {parts ? (
          <>
            {parts[0]}
            <mark className="bg-yellow-200">{parts[1]}</mark>
            {parts[2]}
          </>
        ) : (
          text
        )}
      </pre>
      <p className="mt-1 text-xs text-gray-500">{sourceFile}</p>
    </div>
  );
}
