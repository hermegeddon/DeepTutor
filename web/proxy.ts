import { NextRequest, NextResponse } from "next/server";

const AUTH_ENABLED = process.env.NEXT_PUBLIC_AUTH_ENABLED === "true";
const LOGIN_PATH = "/login";
const COOKIE_NAME = "dt_token";

export function proxy(req: NextRequest) {
  // Auth is disabled (default) — let everything through
  if (!AUTH_ENABLED) return NextResponse.next();

  const { pathname } = req.nextUrl;

  // Always allow auth pages and Next.js internals
  if (
    pathname.startsWith(LOGIN_PATH) ||
    pathname.startsWith("/register") ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon")
  ) {
    return NextResponse.next();
  }

  const token = req.cookies.get(COOKIE_NAME)?.value;

  // No token — redirect to login, preserving the intended destination
  if (!token) {
    const loginUrl = req.nextUrl.clone();
    loginUrl.pathname = LOGIN_PATH;
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  // Verify token structure (JWT: header.payload.signature)
  const parts = token.split(".");
  if (parts.length !== 3) {
    const loginUrl = req.nextUrl.clone();
    loginUrl.pathname = LOGIN_PATH;
    loginUrl.searchParams.set("next", pathname);
    const response = NextResponse.redirect(loginUrl);
    response.cookies.delete(COOKIE_NAME);
    return response;
  }

  // Check expiry from payload (base64url-decoded JSON)
  try {
    const payload = JSON.parse(
      Buffer.from(parts[1], "base64url").toString("utf-8")
    );
    if (payload.exp && Date.now() >= payload.exp * 1000) {
      const loginUrl = req.nextUrl.clone();
      loginUrl.pathname = LOGIN_PATH;
      loginUrl.searchParams.set("next", pathname);
      const response = NextResponse.redirect(loginUrl);
      response.cookies.delete(COOKIE_NAME);
      return response;
    }
  } catch {
    // Malformed payload — treat as invalid
    const loginUrl = req.nextUrl.clone();
    loginUrl.pathname = LOGIN_PATH;
    loginUrl.searchParams.set("next", pathname);
    const response = NextResponse.redirect(loginUrl);
    response.cookies.delete(COOKIE_NAME);
    return response;
  }

  return NextResponse.next();
}

export const config = {
  // Run on all page routes, skip API and static assets
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
