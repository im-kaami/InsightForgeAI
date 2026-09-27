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
  sql,
}: {
  columns: string[];
  rows: Record<string, unknown>[];
  totalRows?: number;
  sql?: string;
}) {
  const [all, setAll] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const visible = rows.slice(0, all ? 200 : 20);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {totalRows !== undefined && <span>{totalRows.toLocaleString()} total rows</span>}
        {rows.length > 20 && (
          <Button size="sm" variant="ghost" onClick={() => setAll(!all)}>
            {all ? "Show less" : "Show all"}
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
