import { LoginView } from "@/components/auth/LoginView";
import { accessContactEmail } from "@/lib/server/contact";
import { safeNextPath } from "@/lib/safe-redirect";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

const first = (value: string | string[] | undefined): string | null =>
  (Array.isArray(value) ? value[0] : value) ?? null;

/** `/login` (newplan §7.7). `?error=` maps to allow-listed messages only; `?next=` must be same-origin. */
export default function LoginPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <LoginView
      errorCode={first(searchParams.error)}
      next={safeNextPath(first(searchParams.next))}
      contactEmail={accessContactEmail()}
      year={new Date().getFullYear()}
    />
  );
}
