"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { DataProfile } from "@/lib/api";

function count(value: number | null | undefined) {
  return value === null || value === undefined ? "Unknown" : value.toLocaleString();
}

export function DataQuality({ profile }: { profile?: DataProfile | null }) {
  if (!profile) return null;
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
        {(profile.warnings ?? []).map((warning) => (
          <div key={warning} className="rounded-md border border-amber-500/30 p-3 text-sm">
            {warning}
          </div>
        ))}
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
