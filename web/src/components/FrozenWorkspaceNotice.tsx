export interface FrozenWorkspaceNoticeProps {
  billingUrl?: string;
}

export function FrozenWorkspaceNotice({
  billingUrl,
}: FrozenWorkspaceNoticeProps) {
  return (
    <div
      className="flex flex-col gap-3 rounded-lg border border-amber-400/60 bg-amber-50 px-4 py-3 text-sm text-amber-950 shadow-sm sm:flex-row sm:items-center sm:justify-between"
      role="status"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-semibold">Your saved research is available</p>
          <span className="rounded-full border border-amber-600/40 bg-amber-100 px-2 py-0.5 text-[0.6875rem] font-semibold uppercase tracking-wide text-amber-900">
            Read-only
          </span>
        </div>
        <p className="mt-1 text-xs leading-5 text-amber-900">
          Browse and copy your existing research here. Upgrade to run new
          research and continue your work.
        </p>
      </div>
      {billingUrl ? (
        <a
          href={billingUrl}
          className="inline-flex min-h-9 shrink-0 items-center justify-center rounded border border-emerald-800 bg-emerald-800 px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-emerald-900"
        >
          Upgrade to Researcher
        </a>
      ) : null}
    </div>
  );
}