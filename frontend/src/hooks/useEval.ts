import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";

/** Treat a 404 as "no results yet" (null) instead of an error. */
async function orNull<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

/** Latest retrieval evaluation results. */
export function useRetrievalEval() {
  return useQuery({
    queryKey: ["eval", "retrieval"],
    queryFn: () => orNull(api.evalRetrieval),
    retry: false,
  });
}

/** Latest extraction evaluation results. */
export function useExtractionEval() {
  return useQuery({
    queryKey: ["eval", "extraction"],
    queryFn: () => orNull(api.evalExtraction),
    retry: false,
  });
}
