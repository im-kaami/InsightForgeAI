"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { datasets, type DataProfile } from "@/lib/api";

type Column = NonNullable<DataProfile["tables"]>[number]["columns"] extends
  (infer Item)[] | undefined
  ? Item
  : never;

function count(value: number | null | undefined) {
  return value === null || value === undefined ? "Unknown" : value.toLocaleString();
}

function number(value: number | null | undefined) {
  return value === null || value === undefined
    ? "?"
    : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function Distribution({ counts }: { counts: number[] }) {
  const highest = Math.max(...counts, 1);
  return (
    <div className="flex h-6 items-end gap-px" role="img" aria-label="Value distribution">
      {counts.map((value, index) => (
        <div
          key={index}
          className="w-1.5 rounded-sm bg-primary/60"
          style={{ height: `${Math.max(4, (value / highest) * 100)}%` }}
        />
      ))}
    </div>
  );
}

function Details({ column }: { column: Column }) {
  if (column.kind === "number" && column.min_value !== null && column.min_value !== undefined)
    return (
      <div className="space-y-1">
        <div>
          {column.min_value} to {column.max_value} · median {number(column.median)}
        </div>
        {(column.histogram ?? []).length > 0 && <Distribution counts={column.histogram ?? []} />}
      </div>
    );
  if (column.kind === "date" && column.min_value)
    return (
      <span>
        {column.min_value} to {column.max_value}
      </span>
    );
  if (column.kind === "text" && column.sensitivity) return <span>Hidden (sensitive)</span>;
  if ((column.top_values ?? []).length > 0)
    return (
      <span>
        {(column.top_values ?? [])
          .slice(0, 3)
          .map((item) => `${item.value} (${item.count.toLocaleString()})`)
          .join(", ")}
      </span>
    );
  return <span>—</span>;
}

export function DataQuality({
  profile,
  datasetId,
  versionId,
  onRefreshed,
}: {
  profile?: DataProfile | null;
  datasetId?: string;
  versionId?: string | null;
  onRefreshed?: () => void;
}) {
  const [refreshing, setRefreshing] = useState(false);
  if (!profile) return null;
  const outdated = (profile.profile_version ?? 1) < 2;
  const checks = (profile.tables ?? []).flatMap((table) =>
    (table.columns ?? []).flatMap((column) =>
      (column.alerts ?? []).map((alert) => `${table.name}.${column.name}: ${alert}`),
    ),
  );

  async function refresh() {
    if (!datasetId || !versionId) return;
    setRefreshing(true);
    try {
      await datasets.refreshProfile(datasetId, versionId);
      onRefreshed?.();
      toast.success("Health check updated");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update the health check");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Data quality</CardTitle>
        <Badge variant={profile.complete ? "secondary" : "outline"}>
          {profile.complete ? "Complete profile" : "Incomplete profile"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Quality checks describe the imported data; they do not certify its accuracy.
        </p>
        <p className="text-sm text-muted-foreground">
          Source freshness: unknown. Import time is not proof that the source is up to date.
        </p>
        {outdated && datasetId && versionId && (
          <div className="flex flex-wrap items-center gap-3 rounded-md border p-3 text-sm">
            <span>
              This health check predates the newer checks for value ranges, outliers, numbers stored
              as text and missing dates.
            </span>
            <Button size="sm" variant="outline" disabled={refreshing} onClick={refresh}>
              {refreshing ? "Updating..." : "Update health check"}
            </Button>
          </div>
        )}
        {(profile.warnings ?? []).map((warning) => (
          <div key={warning} className="rounded-md border border-amber-500/30 p-3 text-sm">
            {warning}
          </div>
        ))}
        {!outdated && (
          <div className="space-y-1 text-sm" data-testid="quality-checks">
            <h3 className="font-medium">
              {checks.length > 0
                ? `${checks.length} check${checks.length === 1 ? "" : "s"} to review`
                : "No issues found by the automatic checks"}
            </h3>
            {checks.length > 0 && (
              <ul className="list-disc space-y-0.5 pl-5">
                {checks.map((check) => (
                  <li key={check}>{check}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        {(profile.tables ?? []).map((table) => (
          <section key={table.name} className="space-y-2">
            <div className="flex flex-wrap gap-4 text-sm">
              <h3 className="font-medium">{table.name}</h3>
              <span>Imported rows: {count(table.row_count)}</span>
              <span>Exact duplicate rows: {count(table.duplicate_rows)}</span>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Column</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Missing</TableHead>
                  <TableHead>Missing %</TableHead>
                  <TableHead>Distinct</TableHead>
                  <TableHead>Repeated values</TableHead>
                  <TableHead>Values</TableHead>
                  <TableHead>Checks</TableHead>
                  <TableHead>Sensitivity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(table.columns ?? []).map((column) => (
                  <TableRow key={column.name}>
                    <TableCell>{column.name}</TableCell>
                    <TableCell>{column.dtype}</TableCell>
                    <TableCell>{count(column.null_count)}</TableCell>
                    <TableCell>
                      {column.null_fraction === null || column.null_fraction === undefined
                        ? "Unknown"
                        : `${(column.null_fraction * 100).toFixed(1)}%`}
                    </TableCell>
                    <TableCell>{count(column.distinct_count)}</TableCell>
                    <TableCell>{count(column.repeated_non_null_count)}</TableCell>
                    <TableCell className="max-w-64 whitespace-normal text-xs">
                      <Details column={column} />
                    </TableCell>
                    <TableCell className="max-w-64 whitespace-normal text-xs">
                      {(column.alerts ?? []).length > 0 ? (
                        <ul className="space-y-0.5 text-amber-700 dark:text-amber-400">
                          {(column.alerts ?? []).map((alert) => (
                            <li key={alert}>{alert}</li>
                          ))}
                        </ul>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell>
                      {column.sensitivity ? (
                        <Badge variant="outline">{column.sensitivity}</Badge>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        ))}
      </CardContent>
    </Card>
  );
}
