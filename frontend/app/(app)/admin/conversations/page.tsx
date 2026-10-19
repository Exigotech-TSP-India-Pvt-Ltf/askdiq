import { redirect } from "next/navigation";
import { audiencePageHref } from "@/lib/audience-routes";

// Render per request so the redirect carries a Location header (see admin/evaluation).
export const dynamic = "force-dynamic";

// Visitors and Conversation Logs are one page now: old links and bookmarks
// land on its Conversations tab with their filters (next.config.js redirects first).
export default function LegacyConversationsPage({
  searchParams,
}: {
  searchParams?: Record<string, string | string[] | undefined>;
}): never {
  redirect(audiencePageHref("conversations", searchParams ?? {}));
}
