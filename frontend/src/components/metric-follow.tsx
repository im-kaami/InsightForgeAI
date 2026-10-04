"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { follows as api, type Follow, type Metric } from "@/lib/api";

const SCHEDULES = [
  { value: "", label: "No schedule" },
  { value: "0 8 * * *", label: "Every day at 08:00" },
  { value: "0 8 * * 1", label: "Every Monday at 08:00" },
  { value: "0 8 1 * *", label: "On the 1st of each month at 08:00" },
];
const scheduleLabel = (cron: string | null | undefined) =>
  SCHEDULES.find((item) => item.value === (cron ?? ""))?.label ?? `Cron ${cron}`;
const browserTimezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

export function MetricFollow({
  datasetId,
  metric,
  items,
}: {
  datasetId: string;
  metric: Metric;
  items: Follow[];
}) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    days: "30",
    threshold: "10",
    cron: "",
    onNewData: true,
    group: "",
  });
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  async function act(action: () => Promise<unknown>, done?: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await action();
      await client.invalidateQueries({ queryKey: ["follows", datasetId] });
      await client.invalidateQueries({ queryKey: ["metric-alerts"] });
      if (done) toast.success(done);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Something went wrong");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function create() {
    const days = Number(form.days);
    const threshold = Number(form.threshold);
    if (!Number.isInteger(days) || days < 1 || days > 366) {
      toast.error("Choose a window of 1 to 366 days");
      return;
    }
    if (!(threshold > 0)) {
      toast.error("Choose an alert threshold above 0%");
      return;
    }
    void act(
      async () => {
        await api.create(datasetId, {
          metric: metric.name,
          group_by: form.group || null,
          days,
          threshold_percent: threshold,
          cron: form.cron || null,
          timezone: browserTimezone(),
          on_new_data: form.onNewData,
          enabled: true,
        });
        setOpen(false);
      },
      `Following ${metric.label || metric.name}`,
    );
  }

  return (
    <div className="w-full space-y-2" data-testid="metric-follow">
      {items.map((item) => {
        const check = item.last_check;
        return (
          <div key={item.id} className="space-y-1 rounded-md border p-2 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary">Following</Badge>
              <span>
                Last {item.days} days{item.group_by ? ` by ${item.group_by}` : ""}; alert at{" "}
                {item.threshold_percent}% change; {scheduleLabel(item.cron)}
                {item.on_new_data ? "; and on new data" : ""}
              </span>
              <span className="flex-1" />
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => act(() => api.check(item.id), "Check finished")}
              >
                Check now
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Stop following ${metric.name}`}
                disabled={busy}
                onClick={() => act(() => api.remove(item.id), "Stopped following")}
              >
                Stop following
              </Button>
            </div>
            {check ? (
              <div data-testid="follow-last-check" className="flex flex-wrap items-center gap-2">
                <Badge
                  variant={
                    check.alert && !check.acknowledged_at
                      ? "destructive"
                      : check.status === "failed"
                        ? "outline"
                        : "secondary"
                  }
                >
                  {check.alert
                    ? check.status === "failed"
                      ? "Check failed"
                      : "Alert"
                    : "No alert"}
                </Badge>
                <span>{check.message}</span>
                {check.session_id ? (
                  <Link className="underline" href={`/sessions/${check.session_id}`}>
                    Open report
                  </Link>
                ) : null}
                {check.alert && !check.acknowledged_at ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => act(() => api.acknowledge(item.id, check.id), "Alert dismissed")}
                  >
                    Dismiss
                  </Button>
                ) : null}
              </div>
            ) : (
              <p className="text-muted-foreground">Not checked yet.</p>
            )}
          </div>
        );
      })}
      {!open ? (
        <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
          Follow
        </Button>
      ) : (
        <div
          data-testid="metric-follow-form"
          className="flex flex-wrap items-end gap-2 rounded-md border p-2"
        >
          <label className="space-y-1">
            <span className="block text-xs text-muted-foreground">Window (days)</span>
            <Input
              aria-label="Window (days)"
              className="w-24"
              inputMode="numeric"
              value={form.days}
              onChange={(event) => setForm({ ...form, days: event.target.value })}
            />
          </label>
          <label className="space-y-1">
            <span className="block text-xs text-muted-foreground">Alert at change of (%)</span>
            <Input
              aria-label="Alert at change of (%)"
              className="w-24"
              inputMode="decimal"
              value={form.threshold}
              onChange={(event) => setForm({ ...form, threshold: event.target.value })}
            />
          </label>
          <label className="space-y-1">
            <span className="block text-xs text-muted-foreground">Split by (optional)</span>
            <select
              aria-label="Follow split by"
              className="h-9 rounded-md border bg-background px-2 text-sm"
              value={form.group}
              onChange={(event) => setForm({ ...form, group: event.target.value })}
            >
              <option value="">No split</option>
              {(metric.dimensions ?? []).map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="block text-xs text-muted-foreground">Check</span>
            <select
              aria-label="Check schedule"
              className="h-9 rounded-md border bg-background px-2 text-sm"
              value={form.cron}
              onChange={(event) => setForm({ ...form, cron: event.target.value })}
            >
              {SCHEDULES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1 text-xs">
            <input
              type="checkbox"
              checked={form.onNewData}
              onChange={(event) => setForm({ ...form, onNewData: event.target.checked })}
            />
            Also check when new data is confirmed
          </label>
          <Button type="button" size="sm" onClick={create} disabled={busy}>
            Start following
          </Button>
          <p className="w-full text-xs text-muted-foreground">
            Each check runs the checked report for the last {form.days || "N"} days that have data,
            compared with the {form.days || "N"} days before, and alerts when the total changes by
            at least the threshold or the check fails. Tested code does all of it; no AI model is
            used. Alerts appear in the sidebar.
          </p>
        </div>
      )}
    </div>
  );
}
