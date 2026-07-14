import { Typography } from "@nous-research/ui/ui/components/typography/index";
import type { StatusResponse } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { DashboardBranding } from "@/lib/branding";
import { CreditCard, ExternalLink, LogOut, UserRound } from "lucide-react";
import { api } from "@/lib/api";
import { useState } from "react";

export function SidebarFooter({ branding, status }: SidebarFooterProps) {
  const [isSigningOut, setIsSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState("");

  const handleSignOut = async () => {
    setIsSigningOut(true);
    setSignOutError("");
    try {
      await api.logout(branding.orgUrl);
    } catch {
      setIsSigningOut(false);
      setSignOutError("Could not sign out. Please try again.");
    }
  };

  const accountActionClass = cn(
    "flex min-w-0 items-center justify-center gap-2 rounded-md border border-current/10",
    "bg-current/[0.025] px-2.5 py-2 text-xs font-medium text-text-secondary",
    "transition-colors hover:border-current/20 hover:bg-current/[0.08] hover:text-midground",
    "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
  );

  return (
    <div className="shrink-0 border-t border-current/10">
      {(branding.accountUrl || branding.billingUrl) ? (
        <div className="space-y-2 px-3 py-3">
          <div className="grid grid-cols-2 gap-2">
            {branding.accountUrl ? (
              <a
                href={branding.accountUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={accountActionClass}
              >
                <UserRound className="h-4 w-4 shrink-0" />
                <span className="truncate">Account</span>
              </a>
            ) : null}
            {branding.billingUrl ? (
              <a
                href={branding.billingUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={accountActionClass}
              >
                <CreditCard className="h-4 w-4 shrink-0" />
                <span className="truncate">Billing</span>
              </a>
            ) : null}
          </div>
          {branding.product === "oxaide" ? (
            <button
              type="button"
              onClick={() => void handleSignOut()}
              disabled={isSigningOut}
              className={cn(
                accountActionClass,
                "w-full disabled:cursor-wait disabled:opacity-60",
              )}
            >
              <LogOut className="h-4 w-4 shrink-0" />
              {isSigningOut ? "Signing out…" : "Sign out"}
            </button>
          ) : null}
          {signOutError ? (
            <p className="px-1 text-center text-[11px] leading-4 text-red-300" role="alert">
              {signOutError}
            </p>
          ) : null}
        </div>
      ) : null}
      <div className="flex items-center justify-between gap-2 border-t border-current/10 px-5 py-2.5">
        {branding.product !== "oxaide" ? <Typography
          className="font-mono-ui text-xs tabular-nums tracking-[0.08em] text-text-tertiary lowercase"
        >
          {status?.version != null ? `v${status.version}` : "—"}
        </Typography> : <span />}

        <a
          href={branding.orgUrl}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            "inline-flex items-center gap-1 font-sans text-display text-xs tracking-[0.12em] text-midground",
            "transition-opacity hover:opacity-90",
            "focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
          )}
        >
          {branding.orgName} <ExternalLink className="h-3 w-3" />
        </a>
      </div>
    </div>
  );
}

interface SidebarFooterProps {
  branding: DashboardBranding;
  status: StatusResponse | null;
}
