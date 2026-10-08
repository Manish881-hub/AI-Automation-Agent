// Single place for the backend base URL.
// Defaults to same-origin ('') so calls go through the Vite dev proxy
// (/api -> backend). Set VITE_API_URL=http://localhost:8001 to hit the
// backend directly (CORS is enabled server-side).
export const API_BASE: string =
  (import.meta as unknown as { env?: Record<string, string | undefined> }).env
    ?.VITE_API_URL ?? "";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}
