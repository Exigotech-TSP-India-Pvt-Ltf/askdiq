import { LoginView } from "@/components/auth/LoginView";
import { accessContactEmail } from "@/lib/server/contact";
import { safeNextPath } from "@/lib/safe-redirect";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

const first = (value: string | string[] | undefined): string | null =>
  (Array.isArray(value) ? value[0] : value) ?? null;

/** `/login`. `?error=` maps to allow-listed messages only; `?next=` must be same-origin. */
export default async function LoginPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  return (
    <LoginView
      errorCode={first(query.error)}
      next={safeNextPath(first(query.next))}
      contactEmail={accessContactEmail()}
      year={new Date().getFullYear()}
    />
  );
}
