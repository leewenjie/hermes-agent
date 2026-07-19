import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CalendarClock,
  Edit3,
  Pause,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  FileText,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { ScheduleBuilder } from "@/components/ScheduleBuilder";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api, type ResearchSchedule, type ResearchScheduleOccurrence } from "@/lib/api";
import {
  buildScheduleString,
  DEFAULT_SCHEDULE_STATE,
  parseScheduleString,
  type ScheduleBuilderState,
} from "@/lib/schedule";

const EMPTY_FORM = {
  name: "",
  prompt: "",
};

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "Not yet";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      });
}

function readableSchedule(schedule: ResearchSchedule): string {
  const { kind, minutes, expr, run_at: runAt } = schedule.schedule;
  if (kind === "interval" && typeof minutes === "number") {
    if (minutes % 1440 === 0) {
      const days = minutes / 1440;
      return `Every ${days} day${days === 1 ? "" : "s"}`;
    }
    if (minutes % 60 === 0) {
      const hours = minutes / 60;
      return `Every ${hours} hour${hours === 1 ? "" : "s"}`;
    }
    return `Every ${minutes} minute${minutes === 1 ? "" : "s"}`;
  }
  if (kind === "once" && runAt) return `Once · ${formatTimestamp(runAt)}`;
  return expr || schedule.schedule_display || schedule.schedule_input;
}

export default function ScheduledResearchPage() {
  const [schedules, setSchedules] = useState<ResearchSchedule[]>([]);
  const [occurrences, setOccurrences] = useState<ResearchScheduleOccurrence[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [pendingMutation, setPendingMutation] = useState<{ fingerprint: string; requestId: string } | null>(null);
  const [scheduleState, setScheduleState] = useState<ScheduleBuilderState>({
    ...DEFAULT_SCHEDULE_STATE,
    intervalValue: 1,
    intervalUnit: "days",
  });
  const { toast, showToast } = useToast();
  const { setTitle } = usePageHeader();

  useEffect(() => {
    setTitle("Scheduled research");
    return () => setTitle(null);
  }, [setTitle]);

  const scheduleValue = useMemo(
    () => buildScheduleString(scheduleState),
    [scheduleState],
  );

  const load = useCallback(async () => {
    try {
      const [scheduleItems, history] = await Promise.all([
        api.getResearchSchedules(),
        api.getResearchScheduleOccurrences(),
      ]);
      setSchedules(scheduleItems);
      setOccurrences(history.occurrences);
    } catch (error) {
      showToast(`Could not load scheduled research: ${error}`, "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    let active = true;

    void Promise.all([api.getResearchSchedules(), api.getResearchScheduleOccurrences()])
      .then(([items, history]) => {
        if (active) {
          setSchedules(items);
          setOccurrences(history.occurrences);
        }
      })
      .catch((error) => {
        if (active) {
          showToast(`Could not load scheduled research: ${error}`, "error");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [showToast]);

  const resetForm = useCallback(() => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setScheduleState({
      ...DEFAULT_SCHEDULE_STATE,
      intervalValue: 1,
      intervalUnit: "days",
    });
  }, []);

  const startEditing = useCallback((item: ResearchSchedule) => {
    setEditingId(item.id);
    setForm({ name: item.name, prompt: item.prompt });
    setScheduleState(parseScheduleString(item.schedule_input));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const submit = useCallback(async () => {
    const prompt = form.prompt.trim();
    if (!prompt || !scheduleValue) {
      showToast("Add research instructions and a valid schedule.", "error");
      return;
    }
    setSaving(true);
    try {
      const fingerprint = JSON.stringify({ editingId, name: form.name.trim(), prompt, schedule: scheduleValue });
      const requestId = pendingMutation?.fingerprint === fingerprint
        ? pendingMutation.requestId
        : crypto.randomUUID();
      setPendingMutation({ fingerprint, requestId });
      const payload = {
        name: form.name.trim(),
        prompt,
        schedule: scheduleValue,
        request_id: requestId,
      };
      if (editingId) {
        await api.updateResearchSchedule(editingId, payload);
        showToast("Research schedule updated", "success");
      } else {
        await api.createResearchSchedule(payload);
        showToast("Research schedule created", "success");
      }
      resetForm();
      setPendingMutation(null);
      await load();
    } catch (error) {
      showToast(`Could not save schedule: ${error}`, "error");
    } finally {
      setSaving(false);
    }
  }, [editingId, form, load, pendingMutation, resetForm, scheduleValue, showToast]);

  const toggle = useCallback(
    async (item: ResearchSchedule) => {
      setWorkingId(item.id);
      try {
        const fingerprint = `${item.enabled ? "pause" : "resume"}:${item.id}`;
        const requestId = pendingMutation?.fingerprint === fingerprint
          ? pendingMutation.requestId
          : crypto.randomUUID();
        setPendingMutation({ fingerprint, requestId });
        if (item.enabled) await api.pauseResearchSchedule(item.id, requestId);
        else await api.resumeResearchSchedule(item.id, requestId);
        setPendingMutation(null);
        showToast(item.enabled ? "Research paused" : "Research resumed", "success");
        await load();
      } catch (error) {
        showToast(`Could not change schedule: ${error}`, "error");
      } finally {
        setWorkingId(null);
      }
    },
    [load, pendingMutation, showToast],
  );

  const remove = useCallback(
    async (item: ResearchSchedule) => {
      if (!window.confirm(`Delete “${item.name || "Untitled research"}”?`)) return;
      setWorkingId(item.id);
      try {
        const fingerprint = `delete:${item.id}`;
        const requestId = pendingMutation?.fingerprint === fingerprint
          ? pendingMutation.requestId
          : crypto.randomUUID();
        setPendingMutation({ fingerprint, requestId });
        await api.deleteResearchSchedule(item.id, requestId);
        setPendingMutation(null);
        if (editingId === item.id) resetForm();
        showToast("Research schedule deleted", "success");
        await load();
      } catch (error) {
        showToast(`Could not delete schedule: ${error}`, "error");
      } finally {
        setWorkingId(null);
      }
    },
    [editingId, load, pendingMutation, resetForm, showToast],
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <Toast toast={toast} />

      <section className="grid gap-2">
        <div className="flex items-center gap-2 text-primary">
          <CalendarClock className="h-5 w-5" />
          <span className="text-xs font-semibold uppercase tracking-[0.16em]">
            Recurring intelligence
          </span>
        </div>
        <H2>Scheduled research</H2>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
          Ask Oxaide to revisit a thesis, market, catalyst, or watchlist on a
          recurring cadence. Results stay in this research workspace for your review.
        </p>
      </section>

      <Card>
        <CardContent className="grid gap-5 p-5 sm:p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold">
                {editingId ? "Edit scheduled research" : "Create scheduled research"}
              </h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Be specific about the evidence, timeframe, risks, and output you want reviewed.
              </p>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <ShieldCheck className="h-4 w-4 text-emerald-500" />
              Private workspace delivery
            </div>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="research-schedule-name">Name</Label>
            <Input
              id="research-schedule-name"
              maxLength={120}
              placeholder="Weekly BTC liquidity review"
              value={form.name}
              onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="research-schedule-prompt">Research instructions</Label>
            <textarea
              id="research-schedule-prompt"
              rows={6}
              maxLength={12000}
              className="w-full resize-y border border-border bg-background/40 px-3 py-2 text-sm leading-6 shadow-sm placeholder:text-muted-foreground focus-visible:border-foreground/25 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/30"
              placeholder="Review BTC spot and perpetual liquidity, funding, basis, major flows, and any evidence that changes the current thesis. Cite sources and flag missing evidence."
              value={form.prompt}
              onChange={(event) => setForm((current) => ({ ...current, prompt: event.target.value }))}
            />
          </div>

          <ScheduleBuilder value={scheduleState} onChange={setScheduleState} />

          <div className="flex flex-wrap justify-end gap-2">
            {editingId ? (
              <Button outlined disabled={saving} onClick={resetForm}>Cancel</Button>
            ) : null}
            <Button
              disabled={saving || !form.prompt.trim() || !scheduleValue}
              prefix={saving ? <Spinner /> : editingId ? <RefreshCw /> : <Plus />}
              onClick={() => void submit()}
            >
              {editingId ? "Save changes" : "Schedule research"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <section className="grid gap-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold">Your research cadence</h3>
            <p className="text-sm text-muted-foreground">
              {schedules.length} {schedules.length === 1 ? "schedule" : "schedules"}
            </p>
          </div>
          <Button ghost size="sm" disabled={loading} prefix={<RefreshCw />} onClick={() => void load()}>
            Refresh
          </Button>
        </div>

        {loading ? (
          <div className="flex justify-center py-16"><Spinner className="text-2xl" /></div>
        ) : schedules.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-14 text-center">
              <CalendarClock className="h-9 w-9 text-muted-foreground" />
              <div>
                <p className="font-medium">No research scheduled yet</p>
                <p className="mt-1 max-w-md text-sm text-muted-foreground">
                  Create a recurring review above so important evidence changes do not slip by unnoticed.
                </p>
              </div>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-3">
            {schedules.map((item) => {
              const working = workingId === item.id;
              return (
                <Card key={item.id}>
                  <CardContent className="grid gap-4 p-5 sm:grid-cols-[1fr_auto] sm:items-start">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h4 className="font-semibold">{item.name || "Untitled research"}</h4>
                        <Badge>{item.enabled ? "Active" : "Paused"}</Badge>
                        {item.last_status ? <Badge>{item.last_status}</Badge> : null}
                      </div>
                      <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
                        {item.prompt}
                      </p>
                      <dl className="mt-4 grid gap-3 text-xs text-muted-foreground sm:grid-cols-3">
                        <div><dt className="uppercase tracking-wide opacity-70">Cadence</dt><dd className="mt-1 text-foreground">{readableSchedule(item)}</dd></div>
                        <div><dt className="uppercase tracking-wide opacity-70">Next review</dt><dd className="mt-1 text-foreground">{item.enabled ? formatTimestamp(item.next_run_at) : "Paused"}</dd></div>
                        <div><dt className="uppercase tracking-wide opacity-70">Last review</dt><dd className="mt-1 text-foreground">{formatTimestamp(item.last_run_at)}</dd></div>
                      </dl>
                    </div>
                    <div className="flex flex-wrap gap-1 sm:justify-end">
                      <Button ghost size="sm" disabled={working} prefix={<Edit3 />} onClick={() => startEditing(item)}>Edit</Button>
                      <Button ghost size="sm" disabled={working} prefix={item.enabled ? <Pause /> : <Play />} onClick={() => void toggle(item)}>{item.enabled ? "Pause" : "Resume"}</Button>
                      <Button ghost size="sm" disabled={working} prefix={<Trash2 />} onClick={() => void remove(item)}>Delete</Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </section>

      <section className="grid gap-3">
        <div>
          <h3 className="text-base font-semibold">Recent results</h3>
          <p className="text-sm text-muted-foreground">Authoritative execution history from your managed workspace.</p>
        </div>
        {occurrences.length === 0 ? (
          <Card><CardContent className="py-8 text-sm text-muted-foreground">No scheduled runs yet.</CardContent></Card>
        ) : (
          <div className="grid gap-2">
            {occurrences.map((occurrence) => (
              <Card key={occurrence.id}>
                <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">{occurrence.name || "Scheduled research"}</p>
                      <Badge>{occurrence.status}</Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Scheduled for {formatTimestamp(occurrence.nominal_fire_at)}
                      {occurrence.error_message ? ` · ${occurrence.error_message}` : ""}
                    </p>
                  </div>
                  {occurrence.result_artifact_ref ? (
                    <Button
                      outlined
                      size="sm"
                      prefix={<FileText />}
                      onClick={() => window.location.assign(`/files?path=${encodeURIComponent(occurrence.result_artifact_ref || "")}`)}
                    >
                      Open result
                    </Button>
                  ) : null}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
