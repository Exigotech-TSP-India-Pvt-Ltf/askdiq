import { SuspendedView } from "@/components/auth/SuspendedView";
import { accessContactEmail } from "@/lib/server/contact";

export const dynamic = "force-dynamic";

/** Landing page when sign-in is refused because the account is suspended (no session exists). */
export default function SuspendedPage() {
  return <SuspendedView contactEmail={accessContactEmail()} />;
}
