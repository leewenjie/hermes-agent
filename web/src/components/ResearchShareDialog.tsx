import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, ExternalLink, Share2, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type {
  ResearchSharePreview,
  ResearchSharePublished,
  SessionInfo,
} from "@/lib/api";
import { Markdown } from "@/components/Markdown";
import { Button } from "@nous-research/ui/ui/components/button";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@nous-research/ui/ui/components/dialog";
import { Input } from "@nous-research/ui/ui/components/input";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

interface ResearchShareDialogProps {
  session: SessionInfo;
  onClose: () => void;
}

export function ResearchShareDialog({ session, onClose }: ResearchShareDialogProps) {
  const [preview, setPreview] = useState<ResearchSharePreview | null>(null);
  const [published, setPublished] = useState<ResearchSharePublished | null>(null);
  const [title, setTitle] = useState(session.title || session.preview || "Shared Oxaide research");
  const [description, setDescription] = useState("A read-only research conversation shared from Oxaide.");
  const [expiresInDays, setExpiresInDays] = useState<7 | 30 | 90>(30);
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.previewResearchShare(session.id),
      api.listResearchShares(session.id),
    ])
      .then(([result, existing]) => {
        if (cancelled) return;
        setPreview(result);
        const active = existing.shares
          .filter((share) => new Date(share.expires_at).getTime() > Date.now())
          .at(-1);
        if (active) setPublished(active);
        if (!session.title) setTitle(result.title);
      })
      .catch((reason) => {
        if (!cancelled) setError(String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [session.id, session.title]);

  const artifacts = useMemo(
    () => new Map(preview?.snapshot.artifacts.map((artifact) => [artifact.name, artifact]) || []),
    [preview],
  );

  const publish = async () => {
    if (!preview || !confirmed) return;
    setPublishing(true);
    setError(null);
    try {
      setPublished(await api.publishResearchShare({
        sessionId: session.id,
        title: title.trim(),
        description: description.trim() || undefined,
        expiresInDays,
        snapshotSha256: preview.snapshot_sha256,
      }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setPublishing(false);
    }
  };

  const copyLink = async () => {
    if (!published) return;
    await navigator.clipboard.writeText(published.public_url);
  };

  const revoke = async () => {
    if (!published) return;
    setRevoking(true);
    setError(null);
    try {
      await api.revokeResearchShare(published.share_id);
      setPublished(null);
      setConfirmed(false);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setRevoking(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[92vh] max-w-5xl overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Share2 className="h-4 w-4 text-success" /> Share read-only research
          </DialogTitle>
          <DialogDescription>
            This creates a frozen, unlisted snapshot. Later chat messages and private workspace data are not added.
          </DialogDescription>
        </DialogHeader>

        <div className="grid min-h-0 gap-5 overflow-y-auto p-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <aside className="space-y-4">
            <label className="block text-xs font-semibold uppercase tracking-[0.08em] text-text-secondary">
              Public title
              <Input className="mt-2 normal-case" value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} />
            </label>
            <label className="block text-xs font-semibold uppercase tracking-[0.08em] text-text-secondary">
              Description
              <Input className="mt-2 normal-case" value={description} maxLength={500} onChange={(event) => setDescription(event.target.value)} />
            </label>
            <label className="block text-xs font-semibold uppercase tracking-[0.08em] text-text-secondary">
              Link expiry
              <select
                value={expiresInDays}
                onChange={(event) => setExpiresInDays(Number(event.target.value) as 7 | 30 | 90)}
                className="mt-2 h-9 w-full border border-border bg-background px-3 text-sm normal-case text-foreground"
              >
                <option value={7}>7 days</option>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
              </select>
            </label>

            <div className="border border-border bg-background/40 p-3 text-xs leading-5 text-text-secondary">
              Included: user questions, assistant responses, safe generated charts, tables, and source links.
              <br />
              Excluded: system prompts, reasoning, tools, commands, internal paths, credentials, and future messages.
            </div>

            {preview?.warnings.map((warning) => (
              <div key={warning} className="flex gap-2 border border-warning/30 bg-warning/10 p-3 text-xs text-warning">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {warning}
              </div>
            ))}

            {!published && (
              <label className="flex cursor-pointer items-start gap-3 text-xs leading-5 text-foreground">
                <Checkbox checked={confirmed} onCheckedChange={(value) => setConfirmed(value === true)} />
                <span>I reviewed this exact preview and am authorized to publish its contents.</span>
              </label>
            )}
          </aside>

          <section className="min-h-72 border border-border bg-background/30 p-4">
            <div className="mb-4 flex items-center justify-between gap-3 border-b border-border pb-3">
              <span className="text-xs font-semibold uppercase tracking-[0.1em] text-text-secondary">Public preview</span>
              <span className="text-xs text-text-tertiary">Read only · Unlisted</span>
            </div>
            {loading ? (
              <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-text-secondary"><Spinner /> Preparing safe preview…</div>
            ) : error && !preview ? (
              <div className="flex min-h-64 items-center justify-center text-sm text-destructive">{error}</div>
            ) : preview ? (
              <div className="space-y-4">
                {preview.snapshot.messages.map((message, index) => (
                  <article key={`${message.role}-${index}`} className={message.role === "user" ? "border border-border bg-secondary/40 p-4" : "border border-success/25 bg-background p-4"}>
                    <div className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-text-secondary">
                      {message.role === "user" ? "Research question" : "Research response"}
                    </div>
                    <Markdown content={message.content} />
                    {(message.artifacts || []).map((name) => {
                      const artifact = artifacts.get(name);
                      if (!artifact) return null;
                      return (
                        <figure key={name} className="mt-4 border border-border bg-secondary/20 p-3">
                          <img src={`data:${artifact.mime_type};base64,${artifact.data_base64}`} alt={`Generated research artifact: ${name}`} className="mx-auto max-h-96 max-w-full object-contain" />
                          <figcaption className="mt-2 text-center text-xs text-text-tertiary">{name}</figcaption>
                        </figure>
                      );
                    })}
                  </article>
                ))}
              </div>
            ) : null}
          </section>
        </div>

        {error && preview && <div className="mx-4 border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

        <DialogFooter>
          {published ? (
            <>
              <div className="mr-auto flex items-center gap-2 text-sm text-success"><CheckCircle2 className="h-4 w-4" /> Public snapshot ready</div>
              <Button outlined onClick={() => void revoke()} disabled={revoking} prefix={revoking ? <Spinner /> : <Trash2 />}>
                Revoke
              </Button>
              <Button outlined onClick={() => void copyLink()} prefix={<Copy />}>Copy link</Button>
              <Button onClick={() => window.open(published.public_url, "_blank", "noopener,noreferrer")} prefix={<ExternalLink />}>Open link</Button>
            </>
          ) : (
            <>
              <Button outlined onClick={onClose}>Cancel</Button>
              <Button onClick={() => void publish()} disabled={!preview || !confirmed || !title.trim() || publishing} prefix={publishing ? <Spinner /> : <Share2 />}>
                Publish snapshot
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
