import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";

export const recordQueryKey = (bidId: string) => ["record", bidId] as const;

/**
 * Latest BidRecord of a bid. A 404 is a normal "no extraction yet" state and
 * is surfaced as `data === null` rather than an error.
 */
export function useRecord(bidId: string | undefined) {
  return useQuery({
    queryKey: recordQueryKey(bidId ?? ""),
    enabled: Boolean(bidId),
    retry: false,
    queryFn: async () => {
      try {
        return await api.getRecord(bidId as string);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
  });
}
