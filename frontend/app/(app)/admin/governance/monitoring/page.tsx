import { redirect } from "next/navigation";

/** The Monitoring view lives in a tab of /admin/governance (deep-linkable via ?tab=). */
export default function GovernanceMonitoringPage() {
  redirect("/admin/governance?tab=Monitoring");
}
