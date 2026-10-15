import { redirect } from "next/navigation";

/** The Traces view lives in a tab of /admin/governance (deep-linkable via ?tab=). */
export default function GovernanceTracesPage() {
  redirect("/admin/governance?tab=Traces");
}
