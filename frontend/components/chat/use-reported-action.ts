"use client";

import { useCallback } from "react";
import { useToast } from "@/components/ui/ToastProvider";

/** Wraps a chat session action so a failure shows a toast instead of failing silently. */
export function useReportedAction() {
  const toast = useToast();
  return useCallback(
    <A extends unknown[]>(title: string, action: (...args: A) => Promise<void>) =>
      async (...args: A) => {
        try {
          await action(...args);
        } catch (err) {
          toast.show({
            title,
            description: err instanceof Error && err.message ? err.message : "Please try again.",
            tone: "error",
          });
        }
      },
    [toast],
  );
}
