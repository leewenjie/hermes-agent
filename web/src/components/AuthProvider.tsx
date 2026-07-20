import {
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from "react";

import { api, type AuthMeResponse } from "@/lib/api";
import { AuthContext, type AuthContextValue } from "./auth-context";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<AuthMeResponse | null>(null);
  const [hidden, setHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    api
      .getAuthMe()
      .then((data) => {
        if (!cancelled) setMe(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);

        if (message.startsWith("401:") || message.startsWith("403:")) {
          setHidden(true);
          return;
        }

        setError("auth status unavailable");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      accessState: me?.access_state === "frozen" ? "frozen" : "active",
      error,
      hidden,
      loading,
      me,
    }),
    [error, hidden, loading, me],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
