"use client";

import { Badge } from "@/components/ui/badge";
import { DataTable } from "@/components/data-table";
import { Markdown } from "@/components/markdown";
import { Plot } from "@/components/plot";
import type { DashboardItem } from "@/lib/api";

const KIND_LABELS: Record<string, string> = {
  pinned: "Pinned result",
  metric: "Approved metric report",
  question: "Approved question",
};

const when = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString() : "not yet";

export function DashboardTileBody({ item }: { item: DashboardItem }) {
  const snapshot = (item.snapshot ?? {}) as Record<string, unknown>;
  const trust = String(snapshot.trust ?? "");
  const period = snapshot.period as { start: string; end: string } | undefined;
  return (
    <div className="space-y-2 text-sm" data-testid="dashboard-tile-body">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="outline">{KIND_LABELS[item.kind] ?? item.kind}</Badge>
        {trust && trust !== "chart" ? <Badge variant="secondary">{trust}</Badge> : null}
        {period ? (
          <span>
            {period.start} to {period.end}
          </span>
        ) : null}
        <span>
          {item.kind === "pinned" ? "Pinned from a run of" : "Refreshed"}{" "}
          {item.kind === "pinned"
            ? `"${String(snapshot.run_goal ?? "")}"`
            : when(item.refreshed_at)}
        </span>
      </div>
      {item.error ? (
        <div role="alert" className="rounded border border-destructive/30 p-2 text-xs">
          {item.error}
          {snapshot.type ? " The last good result is shown below." : ""}
        </div>
      ) : null}
      {snapshot.type === "table" ? (
        <DataTable
          columns={(snapshot.columns as string[]) ?? []}
          rows={(snapshot.rows as Record<string, unknown>[]) ?? []}
          totalRows={Number(snapshot.total_rows ?? 0)}
          sql={String(snapshot.sql ?? "")}
        />
      ) : null}
      {snapshot.type === "plot" ? (
        <Plot
          figure={(snapshot.figure as { data?: never[]; layout?: object }) ?? {}}
          kind={String(snapshot.kind ?? "")}
        />
      ) : null}
      {snapshot.summary ? (
        <details>
          <summary className="cursor-pointer text-xs">Report summary</summary>
          <Markdown>{String(snapshot.summary)}</Markdown>
        </details>
      ) : null}
    </div>
  );
}
