// Provider-suffixed OIDC redirect URI, e.g. /auth/callback/microsoft-entra-id.
// Some Entra app registrations use this path; it is handled exactly like /auth/callback
// (same state check, same backend exchange, same cookie handling). The redirect URI the
// backend sends to Entra is ENTRA_REDIRECT_URI, so register that exact value in Azure.
export const dynamic = "force-dynamic";
export { GET } from "../route";
