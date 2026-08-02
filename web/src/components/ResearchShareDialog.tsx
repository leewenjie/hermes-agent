import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, ExternalLink, Link2, RotateCcw, Trash2, X } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import type { ResearchSharePreview, ResearchSharePublished, SessionInfo } from "@/lib/api";
import { Markdown } from "@/components/Markdown";
import { Button } from "@nous-research/ui/ui/components/button";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@nous-research/ui/ui/components/dialog";
import { Input } from "@nous-research/ui/ui/components/input";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

interface ResearchShareDialogProps {
  session: Pick<SessionInfo, "id" | "preview" | "title">;
  onClose: () => void;
}

interface ShareError {
  message: string;
  refreshPreview?: boolean;
}

const SHARE_PREVIEW_TIMEOUT_MS = 12_000;
const SHARE_ACTION_TIMEOUT_MS = 15_000;

function describeShareError(reason: unknown): ShareError {
  if (reason instanceof ApiError) {
    if (reason.status === 403) {
      return { message: "This session is not linked to your current account. Close this dialog, reopen or refresh the chat once, then retry sharing." };
    }
    if (reason.status === 409) {
      return {
        message: "The conversation changed after this preview was prepared. Refresh the preview and review the updated snapshot before publishing.",
        refreshPreview: true,
      };
    }
    if (reason.status >= 500) return { message: "The sharing service is temporarily unavailable. Please retry." };
    return { message: reason.detail || "The sharing request could not be completed." };
  }
  if (reason instanceof DOMException && reason.name === "AbortError") {
    return { message: "The sharing request was cancelled." };
  }
  return { message: reason instanceof Error && reason.message ? reason.message : "The sharing request could not be completed." };
}

export function ResearchShareDialog({ session, onClose }: ResearchShareDialogProps) {
  const [preview, setPreview] = useState<ResearchSharePreview | null>(null);
  const [published, setPublished] = useState<ResearchSharePublished | null>(null);
  const [title, setTitle] = useState(session.title || session.preview || "Shared Oxaide research");
  const [description, setDescription] = useState("A read-only research conversation shared from Oxaide.");
  const [expiresInDays, setExpiresInDays] = useState<7 | 30 | 90>(30);
  const [confirmed, setConfirmed] = useState(false);
  const [loadingPreview, setLoadingPreview] = useState(true);
  const [loadingShares, setLoadingShares] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [copied, setCopied] = useState(false);
  const [loadError, setLoadError] = useState<ShareError | null>(null);
  const [existingSharesError, setExistingSharesError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<ShareError | null>(null);
  const loadControllerRef = useRef<AbortController | null>(null);
  const actionControllerRef = useRef<AbortController | null>(null);
  const linkInputRef = useRef<HTMLInputElement>(null);

  const loadShareData = useCallback(() => {
    loadControllerRef.current?.abort();
    const controller = new AbortController();
    loadControllerRef.current = controller;
    let timedOut = false;
    setLoadingPreview(true);
    setLoadingShares(true);
    setLoadError(null);
    setExistingSharesError(null);
    setActionError(null);
    setPreview(null);
    setConfirmed(false);

    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, SHARE_PREVIEW_TIMEOUT_MS);

    const previewRequest = api.previewResearchShare(session.id, { signal: controller.signal })
      .then((result) => {
        if (loadControllerRef.current !== controller) return;
        setPreview(result);
        if (!session.title) setTitle(result.title);
      })
      .catch((reason: unknown) => {
        if (loadControllerRef.current !== controller) return;
        setLoadError(controller.signal.aborted && timedOut
          ? { message: "Preparing the preview took too long. Retry when ready." }
          : describeShareError(reason));
      })
      .finally(() => {
        if (loadControllerRef.current === controller) setLoadingPreview(false);
      });

    const sharesRequest = api.listResearchShares(session.id, controller.signal)
      .then((existing) => {
        if (loadControllerRef.current !== controller) return;
        const active = existing.shares.filter((share) => new Date(share.expires_at).getTime() > Date.now()).at(-1);
        setPublished(active ?? null);
      })
      .catch((reason: unknown) => {
        if (loadControllerRef.current === controller && !controller.signal.aborted) {
          setExistingSharesError(`${describeShareError(reason).message} Existing links could not be checked, but you can still review this preview.`);
        }
      })
      .finally(() => {
        if (loadControllerRef.current === controller) setLoadingShares(false);
      });

    void Promise.allSettled([previewRequest, sharesRequest]).then(() => {
      window.clearTimeout(timeout);
      if (loadControllerRef.current === controller) loadControllerRef.current = null;
    });
  }, [session.id, session.title]);

  useEffect(() => {
    const initialLoad = window.setTimeout(loadShareData, 0);
    return () => {
      window.clearTimeout(initialLoad);
      const loadController = loadControllerRef.current;
      const actionController = actionControllerRef.current;
      loadControllerRef.current = null;
      actionControllerRef.current = null;
      loadController?.abort();
      actionController?.abort();
    };
  }, [loadShareData]);

  const artifacts = useMemo(
    () => new Map(preview?.snapshot.artifacts.map((artifact) => [artifact.name, artifact]) || []),
    [preview],
  );

  const publish = async () => {
    if (!preview || !confirmed) return;
    const controller = new AbortController();
    actionControllerRef.current?.abort();
    actionControllerRef.current = controller;
    setPublishing(true);
    setActionError(null);
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, SHARE_ACTION_TIMEOUT_MS);
    try {
      const result = await api.publishResearchShare({
        sessionId: session.id,
        title: title.trim(),
        description: description.trim() || undefined,
        expiresInDays,
        snapshotSha256: preview.snapshot_sha256,
        signal: controller.signal,
      });
      if (actionControllerRef.current === controller) setPublished(result);
    } catch (reason) {
      if (actionControllerRef.current === controller) {
        setActionError(
          timedOut
            ? { message: "Publishing took too long. Check existing links, then retry." }
            : describeShareError(reason),
        );
      }
    } finally {
      window.clearTimeout(timeout);
      if (actionControllerRef.current === controller) {
        actionControllerRef.current = null;
        setPublishing(false);
      }
    }
  };

  const copyLink = async () => {
    if (!published) return;
    setActionError(null);
    try {
      await navigator.clipboard.writeText(published.public_url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      linkInputRef.current?.focus();
      linkInputRef.current?.select();
      setActionError({ message: "The browser blocked clipboard access. The link is selected so you can copy it manually." });
    }
  };

  const revoke = async () => {
    if (!published) return;
    const controller = new AbortController();
    actionControllerRef.current?.abort();
    actionControllerRef.current = controller;
    setRevoking(true);
    setActionError(null);
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, SHARE_ACTION_TIMEOUT_MS);
    try {
      await api.revokeResearchShare(published.share_id, controller.signal);
      if (actionControllerRef.current !== controller) return;
      setPublished(null);
      setConfirmed(false);
      setConfirmRevoke(false);
    } catch (reason) {
      if (actionControllerRef.current === controller) {
        setActionError(
          timedOut
            ? { message: "Revoking took too long. Check the link status, then retry." }
            : describeShareError(reason),
        );
      }
    } finally {
      window.clearTimeout(timeout);
      if (actionControllerRef.current === controller) {
        actionControllerRef.current = null;
        setRevoking(false);
      }
    }
  };

  const handleClose = () => {
    const loadController = loadControllerRef.current;
    const actionController = actionControllerRef.current;
    loadControllerRef.current = null;
    actionControllerRef.current = null;
    loadController?.abort();
    actionController?.abort();
    onClose();
  };

  const stopLoading = () => {
    const controller = loadControllerRef.current;
    loadControllerRef.current = null;
    controller?.abort();
    setLoadingPreview(false);
    setLoadingShares(false);
    setLoadError({ message: "Preview loading was stopped. Retry when ready." });
  };

  return (
    <>
      <Dialog open onOpenChange={(open) => !open && handleClose()}>
        <DialogContent className="flex max-h-[92vh] w-[calc(100vw-1rem)] max-w-5xl flex-col gap-0 overflow-hidden p-0 sm:w-full">
          <DialogHeader className="shrink-0 border-b border-border px-4 py-4 sm:px-6">
            <DialogTitle className="flex items-center gap-3">
              <img src="/brand/oxaide-wordmark-inverse.svg" alt="Oxaide" className="h-6 w-auto" />
              <span className="sr-only">— </span>
              <span>Share public session</span>
            </DialogTitle>
            <DialogDescription>This creates a frozen, unlisted snapshot. Later chat messages and private workspace data are not added.</DialogDescription>
          </DialogHeader>

          <div className="grid min-h-0 flex-1 gap-5 overflow-y-auto p-3 sm:p-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
            <aside className="space-y-3">
              <label htmlFor="research-share-title" className="block text-xs font-semibold uppercase tracking-[0.08em] text-text-secondary">Public title</label>
              <Input id="research-share-title" value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} className="normal-case" />
              <label htmlFor="research-share-description" className="block text-xs font-semibold uppercase tracking-[0.08em] text-text-secondary">Description</label>
              <textarea id="research-share-description" value={description} maxLength={500} onChange={(event) => setDescription(event.target.value)} className="min-h-20 w-full resize-y border border-border bg-background px-3 py-2 text-sm font-normal normal-case text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring" />
              <label htmlFor="research-share-expiry" className="block text-xs font-semibold uppercase tracking-[0.08em] text-text-secondary">Link expiry</label>
              <select id="research-share-expiry" value={expiresInDays} onChange={(event) => setExpiresInDays(Number(event.target.value) as 7 | 30 | 90)} className="h-9 w-full border border-border bg-background px-3 text-sm normal-case text-foreground">
                <option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option>
              </select>
              <div className="border border-border bg-background/40 p-3 text-xs leading-5 text-text-secondary">
                Included: user questions, assistant responses, safe generated charts, tables, and source links.<br />
                Excluded: system prompts, reasoning, tools, commands, internal paths, credentials, and future messages.
              </div>
              {preview?.warnings.map((warning) => <div key={warning} className="flex gap-2 border border-warning/30 bg-warning/10 p-3 text-xs text-warning"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {warning}</div>)}
              {existingSharesError && <div role="status" className="flex gap-2 border border-warning/30 bg-warning/10 p-3 text-xs text-warning"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {existingSharesError}</div>}
              {published && (
                <div className="space-y-2 border border-success/30 bg-success/5 p-3 text-xs">
                  <div className="flex items-center justify-between font-semibold uppercase tracking-[0.08em] text-success"><span>Public link</span>{loadingShares && <Spinner />}</div>
                  <input
                    ref={linkInputRef}
                    readOnly
                    value={published.public_url}
                    onFocus={(event) => event.currentTarget.select()}
                    aria-label="Public research link"
                    className="h-9 w-full border border-border bg-background px-3 text-sm font-normal normal-case text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                  <div className="text-text-secondary">Expires {new Date(published.expires_at).toLocaleDateString()}</div>
                </div>
              )}
            </aside>

            <section aria-busy={loadingPreview} className="min-h-72 border border-border bg-background/30 p-3 sm:p-4">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3"><span className="text-xs font-semibold uppercase tracking-[0.1em] text-text-secondary">Public preview</span><span className="text-xs text-text-tertiary">Read only · Unlisted</span></div>
              {loadingPreview ? (
                <div className="flex min-h-64 flex-col items-center justify-center gap-3 text-sm text-text-secondary"><span className="inline-flex items-center gap-2"><Spinner /> Preparing safe preview…</span><Button outlined size="sm" onClick={stopLoading} prefix={<X className="h-3.5 w-3.5" />}>Stop loading</Button></div>
              ) : loadError && !preview ? (
                <div role="alert" className="flex min-h-64 flex-col items-center justify-center gap-4 px-4 text-center text-sm"><AlertTriangle className="h-5 w-5 text-destructive" /><p className="max-w-lg text-destructive">{loadError.message}</p><Button outlined size="sm" onClick={loadShareData} prefix={<RotateCcw className="h-4 w-4" />}>Retry preview</Button></div>
              ) : preview ? (
                <div className="space-y-4">{preview.snapshot.messages.map((message, index) => (
                  <article key={`${message.role}-${index}`} className={message.role === "user" ? "border border-border bg-secondary/40 p-4" : "border border-success/25 bg-background p-4"}>
                    <div className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-text-secondary">{message.role === "user" ? "You" : "Oxaide"}</div>
                    <Markdown content={message.content} />
                    {(message.artifacts || []).map((name) => {
                      const artifact = artifacts.get(name);
                      return artifact ? <figure key={name} className="mt-4 border border-border bg-secondary/20 p-3"><img src={`data:${artifact.mime_type};base64,${artifact.data_base64}`} alt={`Generated research artifact: ${name}`} className="mx-auto max-h-96 max-w-full object-contain" /><figcaption className="mt-2 text-center text-xs text-text-tertiary">{name}</figcaption></figure> : null;
                    })}
                  </article>
                ))}</div>
              ) : null}
            </section>
          </div>

          {actionError && <div role="alert" aria-live="polite" className="mx-3 mt-3 flex shrink-0 flex-wrap items-center gap-3 border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive sm:mx-4"><span className="min-w-0 flex-1">{actionError.message}</span>{actionError.refreshPreview && <Button outlined size="sm" onClick={loadShareData} prefix={<RotateCcw className="h-3.5 w-3.5" />}>Refresh preview</Button>}</div>}
          <DialogFooter className="shrink-0 flex-col items-stretch gap-3 border-t border-border bg-background px-4 py-3 sm:flex-row sm:items-center sm:px-6">
            {published ? <><div className="mr-auto flex items-center gap-2 text-sm text-success"><CheckCircle2 className="h-4 w-4" /> Public link ready</div><Button outlined size="sm" onClick={() => setConfirmRevoke(true)} disabled={revoking} prefix={<Trash2 />}>Revoke</Button><Button outlined size="sm" onClick={() => void copyLink()} prefix={copied ? <CheckCircle2 /> : <Copy />}>{copied ? "Copied" : "Copy link"}</Button><Button size="sm" onClick={() => window.open(published.public_url, "_blank", "noopener,noreferrer")} prefix={<ExternalLink />}>Open link</Button></> : <>{preview && <label id="research-share-confirmation" className="mr-auto flex cursor-pointer items-start gap-2 text-xs leading-5 text-foreground sm:max-w-md"><Checkbox checked={confirmed} onCheckedChange={(value) => setConfirmed(value === true)} /><span>I reviewed this exact preview and am authorized to publish its contents.</span></label>}<div className="flex justify-end gap-2"><Button outlined size="sm" onClick={handleClose}>Cancel</Button><Button size="sm" onClick={() => void publish()} disabled={!preview || !confirmed || !title.trim() || publishing} aria-describedby={preview && !confirmed ? "research-share-confirmation" : undefined} prefix={publishing ? <Spinner /> : <Link2 />}>Create public link</Button></div></>}
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog open={confirmRevoke} title="Revoke public link?" description="Anyone using this link will immediately lose access. The private conversation is not deleted." confirmLabel="Revoke link" destructive loading={revoking} onCancel={() => setConfirmRevoke(false)} onConfirm={() => void revoke()} />
    </>
  );
}
