import { redirect } from "next/navigation";

/** The Logs view lives in a tab of /admin/governance (deep-linkable via ?tab=). */
export default function GovernanceLogsPage() {
  redirect("/admin/governance?tab=Logs");
}
