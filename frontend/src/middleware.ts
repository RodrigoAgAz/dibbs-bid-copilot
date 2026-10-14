import { NextResponse, type NextRequest } from "next/server";

const PUBLIC = ["/login", "/signup", "/forgot-password", "/reset-password", "/terms", "/privacy"];

/**
 * Route guard. The csrf_token cookie (path=/, same lifetime as the refresh token) is the
 * "has a session" hint; the API remains the real authority and answers 401 otherwise.
 */
export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const hasSession = Boolean(req.cookies.get("csrf_token")?.value);
  const isPublic = PUBLIC.some((p) => pathname === p || pathname.startsWith(`${p}/`));

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
