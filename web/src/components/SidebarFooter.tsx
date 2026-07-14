import { Typography } from "@nous-research/ui/ui/components/typography/index";
import type { StatusResponse } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { DashboardBranding } from "@/lib/branding";
import { CreditCard, ExternalLink, UserRound } from "lucide-react";

export function SidebarFooter({ branding, status }: SidebarFooterProps) {
  return (
    <div className="shrink-0 border-t border-current/10">
      {(branding.accountUrl || branding.billingUrl) ? (
        <div className="grid grid-cols-2 gap-1 px-3 py-2">
          {branding.accountUrl ? (
            <a
              href={branding.accountUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-1.5 rounded px-2 py-1.5 text-xs text-text-secondary transition-colors hover:bg-current/10 hover:text-midground"
            >
              <UserRound className="h-3.5 w-3.5" /> Account
            </a>
          ) : null}
          {branding.billingUrl ? (
            <a
              href={branding.billingUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-1.5 rounded px-2 py-1.5 text-xs text-text-secondary transition-colors hover:bg-current/10 hover:text-midground"
            >
              <CreditCard className="h-3.5 w-3.5" /> Billing
            </a>
          ) : null}
        </div>
      ) : null}
      <div className="flex items-center justify-between gap-2 border-t border-current/10 px-5 py-2.5">
        <Typography
          className="font-mono-ui text-xs tabular-nums tracking-[0.08em] text-text-tertiary lowercase"
        >
          {status?.version != null ? `v${status.version}` : "—"}
        </Typography>

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
