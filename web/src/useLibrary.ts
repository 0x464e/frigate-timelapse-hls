import { useCallback, useEffect, useState } from "react";
import { parseLibrary } from "./lib";
import type { Library } from "./types";

export function useLibrary() {
  const [library, setLibrary] = useState<Library | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(new Date());
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let active = true;
    let pending = false;
    let controller: AbortController | undefined;
    async function fetchLibrary() {
      setNow(new Date());
      if (document.hidden || pending) return;
      pending = true;
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller?.abort(), 10000);
      try {
        const response = await fetch("/published/library.json", {
          cache: "no-cache",
          signal: controller.signal,
        });
        if (!response.ok)
          throw new Error(
            response.status === 404
              ? "The library is not published yet. Start the generator or run publish-library."
              : `Library unavailable (${response.status}). Try again.`,
          );
        if (
          !response.headers.get("content-type")?.includes("application/json")
        ) {
          throw new Error(
            "The session may have expired. Reload the page to sign in again.",
          );
        }
        const next = parseLibrary(await response.json());
        if (active) {
          setLibrary(next);
          setError("");
        }
      } catch (reason) {
        if (active)
          setError(
            reason instanceof Error
              ? reason.message
              : "Cannot connect to the library",
          );
      } finally {
        clearTimeout(timeout);
        pending = false;
      }
    }
    void fetchLibrary();
    const timer = window.setInterval(fetchLibrary, 10000);
    document.addEventListener("visibilitychange", fetchLibrary);
    return () => {
      active = false;
      clearInterval(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", fetchLibrary);
    };
  }, [revision]);
  return { library, error, now, refresh };
}
