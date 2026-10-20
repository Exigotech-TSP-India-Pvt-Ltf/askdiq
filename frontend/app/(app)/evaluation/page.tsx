import { redirect } from "next/navigation";

// Render per request: a statically prerendered redirect is served as a 307
// without a Location header, so non-browser clients could not follow it.
export const dynamic = "force-dynamic";

// The old Evaluation page is retired: bookmarks land on Live.
export default function EvaluationRedirectPage(): never {
  redirect("/admin/evaluation-live");
}
