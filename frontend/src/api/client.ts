// Typed fetch wrapper. All requests go through the Vite /api proxy in dev.

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

// Mirror of backend Pydantic models — keep in sync with backend/app.
export interface WizardQuestion {
  fact_key: string;
  section: string;
  prompt: string;
  why_we_ask: string;
  clause_ref: string;
  checklist_entry_id: string;
}

export interface Gap {
  entry_id: string;
  section: string;
  missing_fact_key: string;
  clause_ref: string;
  routed_to: "promoter" | "auditor" | "banker" | "system";
  severity: "blocker" | "material" | "minor";
}
