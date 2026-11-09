import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { ComparisonReport } from "@/api/types";

export const comparisonQueryKey = ["comparison", "latest"] as const;

/**
 * Latest saved comparison report. A 404 is the normal "nothing compared yet"
 * state and is surfaced as `data === null` rather than an error.
 */
export function useLatestComparison() {
  return useQuery({
    queryKey: comparisonQueryKey,
    retry: false,
    queryFn: async (): Promise<ComparisonReport | null> => {
      try {
        return await api.getComparison();
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
  });
}

/** Run the comparison agent; the result replaces the cached latest report. */
export function useRunComparison() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (bidIds: string[]) => api.compare({ bid_ids: bidIds }),
    onSuccess: (report) => {
      queryClient.setQueryData<ComparisonReport | null>(comparisonQueryKey, report);
    },
  });
}
