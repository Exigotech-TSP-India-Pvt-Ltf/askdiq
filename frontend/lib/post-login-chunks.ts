/**
 * Code of the routes a user lands on after sign-in: the console shell, the
 * dashboard (admins and operators) and Chat (every role). Module code only;
 * evaluating these modules makes no request.
 */
import type { ChunkLoader } from "@/lib/login-preload";

export const POST_LOGIN_CHUNKS: readonly ChunkLoader[] = [
  () => import("@/app/(app)/layout"),
  () => import("@/app/(app)/admin/page"),
  () => import("@/app/(app)/chat/page"),
];
