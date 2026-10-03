"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { datasets, type Metric } from "@/lib/api";

export function MetricReportForm({ datasetId, metric }: { datasetId: string; metric: Metric }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ start: "", end: "", group: "" });
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  async function start() {
    if (!form.start || !form.end) {
      toast.error("Choose the first and last day of the period");
      return;
    }
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const run = await datasets.metricReport(datasetId, {
        metric: metric.name,
        start_date: form.start,
        end_date: form.end,
        group_by: form.group || null,
      });
      router.push(`/sessions/${run.session_id}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not start the report");
      inFlight.current = false;
      setBusy(false);
    }
  }

  if (!open)
    return (
      <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
        Checked report
      </Button>
    );
  return (
    <div
      data-testid="metric-report-form"
      className="flex w-full flex-wrap items-end gap-2 rounded-md border p-2"
    >
      <label className="space-y-1">
        <span className="block text-xs text-muted-foreground">First day</span>
        <Input
          type="date"
          aria-label="First day"
          value={form.start}
          onChange={(event) => setForm({ ...form, start: event.target.value })}
        />
      </label>
      <label className="space-y-1">
        <span className="block text-xs text-muted-foreground">Last day</span>
        <Input
          type="date"
          aria-label="Last day"
          value={form.end}
          onChange={(event) => setForm({ ...form, end: event.target.value })}
        />
      </label>
      <label className="space-y-1">
        <span className="block text-xs text-muted-foreground">Split by (optional)</span>
        <select
          aria-label="Split by"
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
      <Button type="button" size="sm" onClick={start} disabled={busy}>
        {busy ? "Starting..." : "Run checked report"}
      </Button>
      <p className="w-full text-xs text-muted-foreground">
        Compares the period with the preceding period of the same length. Tested code runs the data
        checks, calculates every number and writes the summary; no AI model is used.
      </p>
    </div>
  );
}
