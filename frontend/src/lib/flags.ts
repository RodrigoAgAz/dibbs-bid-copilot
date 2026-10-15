/**
 * Sign-up and password-reset pages. Off while the product is in development: the only way in is the login page
 * (username login, see SIMPLE_LOGIN_* on the backend). Set NEXT_PUBLIC_AUTH_PAGES=true on the frontend service (a
 * build-time variable: redeploy after changing it) to bring them back.
 */
export const AUTH_PAGES = process.env.NEXT_PUBLIC_AUTH_PAGES === "true";
