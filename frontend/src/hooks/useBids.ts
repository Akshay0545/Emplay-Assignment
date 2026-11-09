import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";

export const bidsQueryKey = ["bids"] as const;

/** All bids known to the API (list page + bid switcher).
 *
 * Polls every 3 s while an upload is still being indexed, so the card flips
 * from "indexing" to "indexed" on its own, then stops polling.
 */
export function useBids() {
  return useQuery({
    queryKey: bidsQueryKey,
    queryFn: api.listBids,
    refetchInterval: (query) =>
      query.state.data?.some((bid) => bid.indexing) ? 3_000 : false,
  });
}

/** Documents and index stats of one bid. */
export function useBidDetail(bidId: string | undefined) {
  return useQuery({
    queryKey: ["bid", bidId],
    queryFn: () => api.getBid(bidId as string),
    enabled: Boolean(bidId),
  });
}

/** How often and how long a remove retries while the server stops the bid's runs. */
export const REMOVE_RETRY_DELAY_MS = 3_000;
export const REMOVE_MAX_RETRIES = 20;

/**
 * Remove a bid everywhere (index, outputs, uploaded files); refreshes the bid
 * list on success. The server first cancels any running extraction or indexing
 * of the bid and answers 409 while that is still winding down (or while
 * Windows briefly locks a file), so a 409 is retried until the removal lands.
 */
export function useRemoveBid() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (bidId: string) => api.removeBid(bidId),
    retry: (failureCount, error) =>
      error instanceof ApiError && error.status === 409 && failureCount < REMOVE_MAX_RETRIES,
    retryDelay: REMOVE_RETRY_DELAY_MS,
    onSuccess: (_result, bidId) => {
      queryClient.removeQueries({ queryKey: ["bid", bidId] });
      void queryClient.invalidateQueries({ queryKey: bidsQueryKey });
    },
  });
}
