import { NextRequest, NextResponse } from "next/server";

export function middleware(request: NextRequest) {
  // Check for the frontend auth marker cookie (set by client after login)
  const token = request.cookies.get("fiboki_auth");
  const isLoginPage = request.nextUrl.pathname === "/login";

  if (!token && !isLoginPage) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  if (token && isLoginPage) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg|api).*)"],
};
