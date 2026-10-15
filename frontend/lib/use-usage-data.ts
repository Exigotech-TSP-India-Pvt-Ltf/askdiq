import { useState, useEffect, useRef, useCallback } from "react";
import type { Audience, UsageSummary } from "@/lib/types";
import { api } from "@/lib/api";

export function useUsageSummary(token: string | null, audience: Audience) {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // In-memory cache by audience
  const cacheRef = useRef<Map<string, { data: UsageSummary; timestamp: number }>>(
    new Map()
  );

  const fetchSummary = useCallback(
    async (force = false) => {
      if (!token) return;

      const cacheKey = audience;
      const cached = cacheRef.current.get(cacheKey);
      const now = Date.now();

      // 15-second SWR cache
      if (!force && cached && now - cached.timestamp < 15_000) {
        setSummary(cached.data);
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setError(null);

      try {
        // Parallel execution for summary and background pre-warming
        const data = await api.getUsageSummary(token, { audience });
        cacheRef.current.set(cacheKey, { data, timestamp: now });
        setSummary(data);
      } catch (err: any) {
        if (err.name !== "AbortError") {
          setError(err.message || "Failed to load usage summary.");
        }
      } finally {
        setIsLoading(false);
      }
    },
    [token, audience]
  );

  useEffect(() => {
    fetchSummary();
  }, [fetchSummary]);

  return {
    summary,
    isLoading,
    error,
    refresh: () => fetchSummary(true),
  };
}
