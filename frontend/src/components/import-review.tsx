"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { DataQuality } from "@/components/data-quality";
import { DataTable } from "@/components/data-table";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { datasets, type Dataset, type DatasetVersion } from "@/lib/api";

export function ImportReview({
  datasetId,
  version,
  onConfirmed,
  onBack,
}: {
  datasetId: string;
  version: DatasetVersion;
  onConfirmed: (dataset: Dataset) => void;
  onBack?: () => void;
}) {
  const tables = version.schema.tables;
  const [tableName, setTableName] = useState(tables[0]?.name ?? "");
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const table = tables.find((item) => item.name === tableName);
  const preview = useQuery({
    queryKey: ["version-preview", datasetId, version.id, tableName],
    queryFn: () => datasets.preview(datasetId, tableName, version.id),
    enabled: Boolean(tableName),
    retry: false,
  });

  async function confirm() {
    if (!reviewed || !tableName || !preview.data || preview.isError || preview.isFetching || busy)
      return;
    setBusy(true);
    try {
      onConfirmed(await datasets.confirmVersion(datasetId, version));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not confirm import");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold">Review import</h2>
        <p className="text-sm text-muted-foreground">
          Confirm inferred columns and original source details before activation.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Original sources</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {version.sources.map((source, index) => (
            <details key={`${String(source.location)}-${index}`} className="rounded border p-3">
              <summary>{String(source.location)}</summary>
              <div className="mt-2 space-y-1 text-muted-foreground">
                <p>SHA256: {String(source.sha256 ?? "Unavailable")}</p>
                <p>Size: {String(source.size_bytes ?? "Unknown")} bytes</p>
                <pre className="overflow-auto text-xs">
                  {JSON.stringify(source.options ?? {}, null, 2)}
                </pre>
              </div>
            </details>
          ))}
        </CardContent>
      </Card>
      <div className="space-y-2">
        <Label htmlFor="review-table">Table</Label>
        <select
          id="review-table"
          className="h-9 w-full rounded-md border bg-background px-3 text-sm"
          value={tableName}
          onChange={(event) => {
            setTableName(event.target.value);
            setReviewed(false);
          }}
        >
          {tables.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </div>
      {table && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Column</TableHead>
              <TableHead>Inferred type</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {table.columns.map((column) => (
              <TableRow key={column.name}>
                <TableCell>{column.name}</TableCell>
                <TableCell>{column.dtype}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {preview.isLoading && <p className="text-sm text-muted-foreground">Loading preview...</p>}
      {preview.isError && (
        <p role="alert" className="text-sm text-destructive">
          {preview.error instanceof Error ? preview.error.message : "Could not load preview"}
        </p>
      )}
      {preview.data && <DataTable columns={preview.data.columns} rows={preview.data.rows} />}
      <DataQuality profile={version.profile} />
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={reviewed}
          onChange={(event) => setReviewed(event.target.checked)}
        />
        I reviewed the import preview
      </label>
      <div className="flex justify-between gap-2">
        {onBack ? (
          <Button type="button" variant="outline" onClick={onBack}>
            Back
          </Button>
        ) : (
          <span />
        )}
        <Button
          type="button"
          disabled={
            !reviewed ||
            !tableName ||
            !preview.data ||
            preview.isError ||
            preview.isFetching ||
            busy
          }
          onClick={confirm}
        >
          {busy ? "Confirming..." : "Confirm import"}
        </Button>
      </div>
    </div>
  );
}
