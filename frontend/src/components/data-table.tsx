"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function DataTable({
  columns,
  rows,
  totalRows,
  truncated = false,
  fullRowCount,
  sql,
}: {
  columns: string[];
  rows: Record<string, unknown>[];
  totalRows?: number;
  truncated?: boolean;
  fullRowCount?: number | null;
  sql?: string;
}) {
  const [all, setAll] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const visible = rows.slice(0, all ? 200 : 20);
  const kept = totalRows ?? rows.length;
  return (
    <div className="space-y-2">
      {truncated && (
        <div
          role="note"
          data-testid="truncation-note"
          className="rounded border border-amber-500/30 p-2 text-xs"
        >
          {fullRowCount
            ? `Result cut off: the query produced ${fullRowCount.toLocaleString()} rows; only the first ${kept.toLocaleString()} were kept.`
            : `Result cut off: only the first ${kept.toLocaleString()} rows were kept, and the full row count could not be computed.`}
        </div>
      )}
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {totalRows !== undefined && (
          <span>
            {truncated
              ? `${totalRows.toLocaleString()} rows kept`
              : `${totalRows.toLocaleString()} total rows`}
            {totalRows > visible.length ? ` · showing ${visible.length.toLocaleString()}` : ""}
          </span>
        )}
        {rows.length > 20 && (
          <Button size="sm" variant="ghost" onClick={() => setAll(!all)}>
            {all
              ? "Show less"
              : totalRows && totalRows > rows.length
                ? `Show ${rows.length}`
                : "Show all"}
          </Button>
        )}
        {sql && (
          <Button size="sm" variant="ghost" onClick={() => setShowSql(!showSql)}>
            Show SQL
          </Button>
        )}
      </div>
      {showSql && <pre className="overflow-auto rounded bg-muted p-3 text-xs">{sql}</pre>}
      <ScrollArea className="max-h-[420px] rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((column) => (
                <TableHead key={column}>{column}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((row, index) => (
              <TableRow key={index}>
                {columns.map((column) => (
                  <TableCell key={column}>
                    {row[column] === null || row[column] === undefined
                      ? "Not available"
                      : String(row[column])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </ScrollArea>
    </div>
  );
}
