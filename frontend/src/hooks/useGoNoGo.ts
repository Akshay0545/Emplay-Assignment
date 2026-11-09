import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { GoNoGoReport } from "@/api/types";

export const goNoGoQueryKey = (bidId: string) => ["go-no-go", bidId] as const;

/**
 * Saved go / no-go assessment of a bid. A 404 is the normal "not assessed
 * yet" state and is surfaced as `data === null` rather than an error.
 */
export function useGoNoGo(bidId: string | undefined) {
  return useQuery({
    queryKey: goNoGoQueryKey(bidId ?? ""),
    enabled: Boolean(bidId),
    retry: false,
    queryFn: async (): Promise<GoNoGoReport | null> => {
      try {
        return await api.getGoNoGo(bidId as string);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
  });
}

/** Run the go / no-go agent for a bid; the result replaces the cached assessment. */
export function useRunGoNoGo(bidId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.runGoNoGo(bidId),
    onSuccess: (report) => {
      queryClient.setQueryData<GoNoGoReport | null>(goNoGoQueryKey(bidId), report);
    },
  });
}
