// Auth.js/NextAuth-style redirect URI, e.g. /api/auth/callback/microsoft-entra-id.
// Lets this app reuse an Entra app registration that other (NextAuth) apps already use.
// Handled exactly like /auth/callback: same state check, backend code exchange and cookies.
// ENTRA_REDIRECT_URI must equal the full URL of this route for the code exchange to succeed.
export const dynamic = "force-dynamic";
export { GET } from "../../../../auth/callback/route";
