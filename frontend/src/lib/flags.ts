/**
 * Sign-in pages (log in, sign up, password reset). Off while the product is in development: every visitor goes
 * straight into the read-only public demo instead. Set NEXT_PUBLIC_AUTH_PAGES=true on the frontend service (a
 * build-time variable: redeploy after changing it) to bring them back. /login?internal=1 still works for staff.
 */
export const AUTH_PAGES = process.env.NEXT_PUBLIC_AUTH_PAGES === "true";
