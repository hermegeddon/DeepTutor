import { apiFetch, apiUrl, parseAuthEnabled } from "@/lib/api";

// ``NEXT_PUBLIC_AUTH_ENABLED`` is inlined at build time and, in the Docker
// image, baked as the ``__NEXT_PUBLIC_AUTH_ENABLED_PLACEHOLDER__`` token that
// ``start-frontend.sh`` rewrites on container start. ``parseAuthEnabled``
// evaluates it at runtime (not a ``=== "true"`` literal comparison) so the
// minifier cannot constant-fold the check and strip the placeholder, which
// would permanently break the runtime rewrite. See lib/api.ts.
export const AUTH_ENABLED = parseAuthEnabled(
  process.env.NEXT_PUBLIC_AUTH_ENABLED,
);

export interface AuthStatus {
  enabled: boolean;
  authenticated: boolean;
  user_id?: string;
  username?: string;
  role?: string;
  is_admin?: boolean;
  /** Avatar marker: "", "icon:<name>:<color>", or "img:<version>". */
  avatar?: string;
}

/**
 * Call the backend to check whether the current session is authenticated.
 * Returns null on network error so callers can decide how to handle it.
 */
export async function fetchAuthStatus(): Promise<AuthStatus | null> {
  try {
    const res = await apiFetch(apiUrl("/api/v1/auth/status"));
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

/**
 * POST credentials to the backend. Returns true on success.
 */
export async function login(
  username: string,
  password: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await apiFetch(apiUrl("/api/v1/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
      // A 401 here means "wrong credentials", not an expired session — handle it
      // inline as a form error instead of triggering the global login redirect.
      skipAuthRedirect: true,
    });

    if (res.ok) return { ok: true };

    const data = await res.json().catch(() => ({}));
    return { ok: false, error: extractDetail(data.detail) ?? "Login failed" };
  } catch {
    return { ok: false, error: "Could not reach the server" };
  }
}

/**
 * Normalise a FastAPI error detail to a plain string.
 * FastAPI can return detail as a string (HTTPException) or as an array of
 * validation error objects (422 Unprocessable Entity).
 */
function extractDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (typeof first === "object" && first !== null && "msg" in first)
      return String((first as { msg: unknown }).msg);
  }
  return "Request failed";
}

/**
 * Register a new account. The first user to register becomes admin.
 */
export async function register(
  username: string,
  password: string,
): Promise<{
  ok: boolean;
  role?: string;
  is_first_user?: boolean;
  error?: string;
}> {
  try {
    const res = await apiFetch(apiUrl("/api/v1/auth/register"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
      // Registration validation failures (e.g. 400/401) should surface inline
      // rather than bounce the user through the global login redirect.
      skipAuthRedirect: true,
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok)
      return { ok: true, role: data.role, is_first_user: data.is_first_user };
    return { ok: false, error: extractDetail(data.detail) };
  } catch {
    return { ok: false, error: "Could not reach the server" };
  }
}

/**
 * Check whether the user store is empty (first user will become admin).
 */
export async function checkIsFirstUser(): Promise<boolean> {
  try {
    const res = await apiFetch(apiUrl("/api/v1/auth/is_first_user"));
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data.is_first_user);
  } catch {
    return false;
  }
}

/**
 * POST to the logout endpoint to clear the session cookie.
 */
export async function logout(): Promise<void> {
  try {
    await apiFetch(apiUrl("/api/v1/auth/logout"), {
      method: "POST",
    });
  } catch {
    // Ignore — we'll redirect regardless
  }
}
