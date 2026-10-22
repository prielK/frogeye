import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api, ApiError, type Competitor } from "@/lib/api/client";

async function fetchCompetitors(): Promise<Competitor[]> {
  const { data, response } = await api.GET("/api/v1/competitors");
  if (!data) throw new ApiError(response.status);
  return data;
}

/** The tracked companies, JFrog included. Reference data: it changes only with the seed. */
export function useCompetitors() {
  return useQuery({ queryKey: ["competitors"], queryFn: fetchCompetitors, staleTime: 10 * 60_000 });
}

/** Slug → competitor, for rendering the slugs other endpoints return. */
export function useCompetitorIndex(): Map<string, Competitor> {
  const { data } = useCompetitors();
  return useMemo(() => new Map((data ?? []).map((c) => [c.slug, c])), [data]);
}

/** Readable fallback while names load, or for a slug no longer tracked: "google-cloud" → "Google cloud". */
export function nameFromSlug(slug: string): string {
  const words = slug.replace(/[-_]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Two-letter mark for an organisation: its own capitals when it has them (GitHub → GH,
 * JFrog → JF), else the first letters of two words (Google Cloud → GC), else two letters.
 */
export function orgInitials(name: string): string {
  const capitals = name.match(/[A-Z]/g) ?? [];
  if (capitals.length >= 2) return capitals.slice(0, 2).join("");
  const words = name.split(/[\s._-]+/).filter(Boolean);
  if (words.length >= 2) return (words[0]![0]! + words[1]![0]!).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}
