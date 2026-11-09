import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { SearchParams } from "@/api/types";

/** Run a search; disabled until the query text is non-empty. */
export function useSearch(params: SearchParams, enabled: boolean) {
  return useQuery({
    queryKey: ["search", params],
    queryFn: () => api.search(params),
    enabled: enabled && params.q.trim().length > 0,
    staleTime: 60_000,
  });
}
