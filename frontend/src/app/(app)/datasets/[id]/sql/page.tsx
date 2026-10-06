"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { DataTable } from "@/components/data-table";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { datasets, type SqlQueryResult } from "@/lib/api";

type SchemaTable = { name: string; columns: { name: string; dtype: string }[] };

function quoteTable(name: string) {
  return name
    .split(".")
    .map((part) => `"${part.replaceAll('"', '""')}"`)
    .join(".");
}

export default function SqlEditorPage() {
  const id = String(useParams().id);
  const dataset = useQuery({ queryKey: ["dataset", id], queryFn: () => datasets.get(id) });
  const tables = (dataset.data?.schema as { tables?: SchemaTable[] })?.tables ?? [];
  const [edited, setEdited] = useState<string | null>(null);
  const sql = edited ?? (tables[0] ? `SELECT * FROM ${quoteTable(tables[0].name)} LIMIT 100` : "");
  const editor = useRef<HTMLTextAreaElement>(null);
  const requestInFlight = useRef(false);
  const [downloading, setDownloading] = useState(false);
  const run = useMutation({
    mutationFn: (text: string) => datasets.runSql(id, text),
    onSettled: () => {
      requestInFlight.current = false;
    },
  });
  const result: SqlQueryResult | undefined = run.data;

  function execute() {
    if (requestInFlight.current || !sql.trim()) return;
    requestInFlight.current = true;
    run.mutate(sql);
  }

  async function download() {
    if (downloading || !sql.trim()) return;
    setDownloading(true);
    try {
      await datasets.downloadSqlCsv(id, sql);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not download the CSV");
    } finally {
      setDownloading(false);
    }
  }

  function insertTable(name: string) {
    const text = quoteTable(name);
    const area = editor.current;
    const start = area?.selectionStart ?? sql.length;
    const end = area?.selectionEnd ?? sql.length;
    setEdited(sql.slice(0, start) + text + sql.slice(end));
    requestAnimationFrame(() => {
      if (!area) return;
      area.focus();
      area.setSelectionRange(start + text.length, start + text.length);
    });
  }

  if (dataset.isError)
    return (
      <p role="alert" className="text-destructive">
        {dataset.error instanceof Error ? dataset.error.message : "Could not load dataset"}
      </p>
    );
  if (!dataset.data) return <p className="text-muted-foreground">Loading dataset...</p>;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold">SQL editor</h1>
          <p className="text-muted-foreground">
            {dataset.data.name} · read-only queries, no AI involved, nothing is saved
          </p>
        </div>
        <Link href={`/datasets/${id}`} className={buttonVariants({ variant: "outline" })}>
          Back to dataset
        </Link>
      </header>
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Tables</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3" data-testid="sql-tables">
            {tables.map((table) => (
              <div key={table.name} className="space-y-1">
                <button
                  type="button"
                  className="w-full truncate rounded px-2 py-1 text-left text-sm font-medium hover:bg-muted"
                  onClick={() => insertTable(table.name)}
                >
                  {table.name}
                </button>
                <ul className="space-y-0.5 pl-4 text-xs text-muted-foreground">
                  {table.columns.map((column) => (
                    <li key={column.name} className="flex justify-between gap-2">
                      <span className="truncate">{column.name}</span>
                      <span>{column.dtype}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </CardContent>
        </Card>
        <div className="space-y-4">
          <Textarea
            ref={editor}
            aria-label="SQL query"
            spellCheck={false}
            className="min-h-40 font-mono text-sm"
            value={sql}
            onChange={(event) => setEdited(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                execute();
              }
            }}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={execute} disabled={run.isPending || !sql.trim()}>
              {run.isPending ? "Running..." : "Run"}
            </Button>
            <Button variant="outline" onClick={download} disabled={downloading || !sql.trim()}>
              {downloading ? "Preparing..." : "Download CSV"}
            </Button>
            <span className="text-xs text-muted-foreground">Ctrl/Cmd+Enter to run</span>
          </div>
          {run.isError && (
            <div
              role="alert"
              className="rounded-md border border-destructive/40 p-3 text-sm text-destructive"
            >
              {run.error instanceof Error ? run.error.message : "The query failed"}
            </div>
          )}
          {result && !run.isError && (
            <div className="space-y-2" data-testid="sql-result">
              <p className="text-sm" data-testid="sql-summary">
                Showing {result.row_count.toLocaleString()} of{" "}
                {(result.truncated && result.full_row_count
                  ? result.full_row_count
                  : result.total_rows
                ).toLocaleString()}{" "}
                rows · {result.elapsed_ms.toLocaleString()} ms
              </p>
              {result.truncated && result.full_row_count && (
                <p role="note" className="text-xs text-amber-600">
                  Result cut at {result.total_rows.toLocaleString()} of{" "}
                  {result.full_row_count.toLocaleString()} rows. Add a LIMIT or a WHERE clause to
                  narrow it.
                </p>
              )}
              <DataTable columns={result.columns} rows={result.rows} maxRows={1000} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
