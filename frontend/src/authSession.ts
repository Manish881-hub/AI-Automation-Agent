// Operator session helpers: every browser API call carries explicit
// same-origin credentials so the HttpOnly session cookie flows, and auth
// status restoration is centralized in one place. The approval token
// itself never appears here — it is typed once into the password field
// and stays server-side.

export type AuthStatus = {
  authenticated: boolean;
  tokenRequired: boolean;
};

type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

const defaultFetch: Fetcher = (url, init) => fetch(url, init);

export function sameOriginRequest(init: RequestInit = {}): RequestInit {
  return { ...init, credentials: "same-origin" };
}

export async function readAuthStatus(
  fetcher: Fetcher = defaultFetch,
): Promise<AuthStatus | null> {
  let res: Response;
  try {
    res = await fetcher("/api/auth/status", { credentials: "same-origin" });
  } catch {
    return null;
  }
  if (!res.ok) return null;
  try {
    const data = await res.json();
    return {
      authenticated: !!data.authenticated,
      tokenRequired: !!data.token_required,
    };
  } catch {
    return null;
  }
}
