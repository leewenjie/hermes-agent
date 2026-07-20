import { createContext, useContext } from "react";

import type { AuthMeResponse } from "@/lib/api";

export type DashboardAccessState = "active" | "frozen";

export interface AuthContextValue {
  accessState: DashboardAccessState;
  error: string | null;
  hidden: boolean;
  loading: boolean;
  me: AuthMeResponse | null;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }

  return context;
}
