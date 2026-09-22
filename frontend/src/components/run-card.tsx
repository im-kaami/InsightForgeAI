"use client";

import { Download } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DataTable } from "@/components/data-table";
import { Markdown } from "@/components/markdown";
import { Plot } from "@/components/plot";
import { StepTimeline } from "@/components/step-timeline";
import { ApiError, runs, type Run } from "@/lib/api";
import type { RunEvent } from "@/lib/sse";

export function RunCard({ run, events = [] }: { run: Run; events?: RunEvent[] }) {
  const artifacts = run.artifacts ?? [];
  async function download(format: string) {
    try {
      await runs.downloadReport(run.id, format);
    } catch (error) {
      toast.error(
        error instanceof ApiError && error.status === 501
          ? "PDF export is not installed"
          : error instanceof Error
            ? error.message
            : "Download failed",
      );
    }
  }
  return (
    <div className="space-y-3">
      <div className="ml-auto max-w-2xl rounded-2xl bg-primary px-4 py-3 text-sm text-primary-foreground">
        {run.goal}
      </div>
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle className="text-base">Analysis</CardTitle>
            <div className="mt-1 flex gap-2">
              {run.used_fallback_plan && <Badge variant="secondary">profiling plan</Badge>}
              <Badge variant="outline">{run.status}</Badge>
            </div>
          </div>
          {run.status === "completed" && (
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button size="sm" variant="outline" />}>
                <Download className="size-4" />
                Download report
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                {[
                  ["Markdown", "md"],
                  ["HTML", "html"],
                  ["PDF", "pdf"],
                ].map(([label, format]) => (
                  <DropdownMenuItem key={format} onClick={() => download(format)}>
                    {label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </CardHeader>
        <CardContent className="space-y-5">
          <StepTimeline events={events} />
          {artifacts.map((artifact, index) => {
            const item = artifact as Record<string, unknown>;
            if (item.type === "table")
              return (
                <section key={index} className="space-y-2">
                  <h3 className="font-medium">{String(item.name)}</h3>
                  <DataTable
                    columns={(item.columns as string[]) ?? []}
                    rows={(item.rows as Record<string, unknown>[]) ?? []}
                    totalRows={Number(item.total_rows)}
                    sql={String(item.sql ?? "")}
                  />
                  {item.csv_path ? (
                    <p className="text-xs text-muted-foreground">CSV saved with run artifacts</p>
                  ) : null}
                </section>
              );
            if (item.type === "plot")
              return (
                <section key={index}>
                  <h3 className="font-medium">{String(item.title || item.name)}</h3>
                  <Plot figure={(item.figure as { data?: never[]; layout?: object }) ?? {}} />
                </section>
              );
            if (item.type === "text")
              return <Markdown key={index}>{String(item.text ?? "")}</Markdown>;
            if (item.type === "error")
              return (
                <div key={index} className="rounded border border-destructive/30 p-3 text-sm">
                  <Badge variant="destructive">Error</Badge>
                  <p className="mt-2">{String(item.message)}</p>
                </div>
              );
            return null;
          })}
          {run.summary &&
            !artifacts.some((item) => (item as Record<string, unknown>).type === "text") && (
              <Markdown>{run.summary}</Markdown>
            )}
          <p className="text-xs text-muted-foreground">
            Tokens:{" "}
            {Object.values(run.token_usage ?? {})
              .reduce((a, b) => a + b, 0)
              .toLocaleString()}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
