import { NextResponse, type NextRequest } from "next/server";
import { AUTH_PAGES } from "@/lib/flags";

const AUTH = ["/login", "/signup", "/forgot-password", "/reset-password"];
const PUBLIC = [...AUTH, "/terms", "/privacy", "/enter"];

/**
 * Route guard. The csrf_token cookie (path=/, same lifetime as the refresh token) is the
 * "has a session" hint; the API remains the real authority and answers 401 otherwise.
 */
export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const hasSession = Boolean(req.cookies.get("csrf_token")?.value);
  const isPublic = PUBLIC.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  const isAuth = AUTH.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  if (!AUTH_PAGES && isAuth && pathname !== "/login") {
    // Sign-up and password reset are off during development: the login page is the only way in.
    return NextResponse.redirect(new URL(hasSession ? "/dashboard" : "/login", req.url));
  }

  if (pathname === "/") {
    // Signed-in users go straight to today's RFQs; everyone else sees the landing page.
    return hasSession ? NextResponse.redirect(new URL("/dashboard", req.url)) : NextResponse.next();
  }
  if (!isPublic && !hasSession) {
    const url = new URL("/login", req.url);
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|svg|ico|txt)$).*)"],
};
