"use client";

import { Download } from "lucide-react";
import { useState } from "react";
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
import { CodeResultCard, type CodeResult } from "@/components/code-result";
import { DataTable } from "@/components/data-table";
import { Markdown } from "@/components/markdown";
import { Plot } from "@/components/plot";
import { StatResultCard, type StatResult } from "@/components/stat-result";
import { StepTimeline } from "@/components/step-timeline";
import { api, ApiError, runs, type Run } from "@/lib/api";
import type { RunEvent } from "@/lib/sse";

const verificationLabels: Record<string, string> = {
  exploratory: "Exploratory analysis",
  checks_passed: "Calculation checks passed",
  needs_review: "Needs review",
  blocked: "Blocked by validation",
};

function ClarificationPrompt({
  question,
  options,
  disabled,
  onAnswer,
}: {
  question: string;
  options: string[];
  disabled: boolean;
  onAnswer?: (question: string, answer: string) => void;
}) {
  const [other, setOther] = useState("");
  return (
    <section data-testid="clarification" className="space-y-3 rounded border p-3 text-sm">
      <p className="font-medium">Before analyzing, the AI needs to know: {question}</p>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <Button
            key={option}
            size="sm"
            variant="outline"
            disabled={disabled || !onAnswer}
            onClick={() => onAnswer?.(question, option)}
          >
            {option}
          </Button>
        ))}
      </div>
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (other.trim()) onAnswer?.(question, other.trim());
        }}
      >
        <input
          aria-label="Other answer"
          className="h-8 flex-1 rounded-md border bg-background px-2 text-sm"
          placeholder="Other answer"
          value={other}
          onChange={(event) => setOther(event.target.value)}
        />
        <Button size="sm" type="submit" disabled={disabled || !onAnswer || !other.trim()}>
          Answer
        </Button>
      </form>
    </section>
  );
}

export function RunCard({
  run,
  events = [],
  answering = false,
  onAnswer,
}: {
  run: Run;
  events?: RunEvent[];
  answering?: boolean;
  onAnswer?: (question: string, answer: string) => void;
}) {
  const artifacts = run.artifacts ?? [];
  const provenance = (run.provenance ?? {}) as Record<string, unknown>;
  const evidence = (provenance.evidence ?? []) as Record<string, unknown>[];
  const checks = (provenance.checks ?? []) as Record<string, unknown>[];
  const metricDefinitions = (provenance.metric_definitions ?? {}) as Record<string, unknown>;
  const assumptions = (provenance.assumptions ?? []) as string[];
  const clarification = provenance.clarification as
    { question: string; options: string[] } | null | undefined;
  const reviews = (provenance.reviews ?? []) as {
    round: number;
    verdict: string;
    reason: string;
    steps: string[];
  }[];
  const trace = (provenance.trace ?? []) as {
    step: string;
    kind: string;
    duration_ms: number;
    ok: boolean;
    details: Record<string, unknown>;
  }[];
  const numberCheck = provenance.number_check as
    { checked: number; matched: number; unmatched: string[] } | null | undefined;
  const notices = Array.from(
    new Set([...(run.warnings ?? []), run.fallback_reason].filter(Boolean) as string[]),
  );
  async function downloadCsv(position: number, name: string) {
    try {
      await api.downloadBlob(`/runs/${run.id}/artifacts/${position}/csv`, `${name}.csv`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "CSV download failed");
    }
  }

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
    <div className="space-y-3" data-testid="run-card">
      <div className="ml-auto max-w-2xl rounded-2xl bg-primary px-4 py-3 text-sm text-primary-foreground">
        {run.goal}
      </div>
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle className="text-base">
              {run.definition_id ? "Approved sales report" : "Exploratory analysis"}
            </CardTitle>
            <div className="mt-1 flex gap-2">
              {run.used_fallback_plan && <Badge variant="secondary">profiling plan</Badge>}
              {provenance.mode === "deep" && (
                <Badge variant="secondary">
                  Deep · {String(provenance.rounds ?? 1)} round
                  {Number(provenance.rounds ?? 1) === 1 ? "" : "s"}
                </Badge>
              )}
              <Badge variant="outline">{run.status}</Badge>
              <Badge variant="outline">
                {verificationLabels[run.verification_status] ?? run.verification_status}
              </Badge>
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
                  ["Notebook (.ipynb)", "ipynb"],
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
          {run.error && (
            <div role="alert" className="rounded border border-destructive/30 p-3 text-sm">
              <Badge variant="destructive">Error</Badge>
              <p className="mt-2">{run.error}</p>
            </div>
          )}
          {notices.map((warning) => (
            <div key={warning} className="rounded border border-amber-500/30 p-3 text-sm">
              {warning}
            </div>
          ))}
          {artifacts.map((artifact, index) => {
            const item = artifact as Record<string, unknown>;
            if (item.type === "table")
              return (
                <section
                  key={index}
                  id={`artifact-${run.id}-${String(item.name)}`}
                  className="space-y-2"
                >
                  <h3 className="font-medium">{String(item.name)}</h3>
                  <DataTable
                    columns={(item.columns as string[]) ?? []}
                    rows={(item.rows as Record<string, unknown>[]) ?? []}
                    totalRows={Number(item.total_rows)}
                    truncated={Boolean(item.truncated)}
                    fullRowCount={item.full_row_count == null ? null : Number(item.full_row_count)}
                    sql={String(item.sql ?? "")}
                  />
                  {item.csv_path ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => downloadCsv(index, String(item.name))}
                    >
                      <Download className="size-4" />
                      Download CSV
                    </Button>
                  ) : null}
                </section>
              );
            if (item.type === "plot")
              return (
                <section key={index}>
                  <h3 className="font-medium">{String(item.title || item.name)}</h3>
                  <Plot
                    figure={(item.figure as { data?: never[]; layout?: object }) ?? {}}
                    kind={String(item.kind)}
                  />
                  {item.note ? (
                    <p className="mt-1 text-xs text-muted-foreground" data-testid="chart-note">
                      {String(item.note)}
                    </p>
                  ) : null}
                </section>
              );
            if (item.type === "text")
              return <Markdown key={index}>{String(item.text ?? "")}</Markdown>;
            if (item.type === "stat")
              return (
                <StatResultCard
                  key={index}
                  result={item as unknown as StatResult}
                  runId={run.status === "completed" ? run.id : undefined}
                  position={index}
                />
              );
            if (item.type === "code")
              return <CodeResultCard key={index} result={item as unknown as CodeResult} />;
            if (item.type === "error")
              return (
                <div
                  key={index}
                  role="alert"
                  className="rounded border border-destructive/30 p-3 text-sm"
                >
                  <Badge variant="destructive">Error</Badge>
                  <p className="mt-2">{String(item.message)}</p>
                </div>
              );
            return null;
          })}
          {clarification ? (
            <ClarificationPrompt
              question={clarification.question}
              options={clarification.options}
              disabled={answering}
              onAnswer={onAnswer}
            />
          ) : (
            run.summary &&
            !artifacts.some((item) => (item as Record<string, unknown>).type === "text") && (
              <Markdown>{run.summary}</Markdown>
            )
          )}
          {numberCheck && numberCheck.checked > 0 && (
            <p
              data-testid="number-check"
              className={
                numberCheck.unmatched.length > 0
                  ? "text-sm text-amber-700 dark:text-amber-400"
                  : "text-sm text-muted-foreground"
              }
            >
              Number check: {numberCheck.matched} of {numberCheck.checked} numbers in the summary
              were found in the results
              {numberCheck.unmatched.length > 0
                ? `; not found: ${numberCheck.unmatched.join(", ")}.`
                : "."}{" "}
              See Evidence and definitions below.
            </p>
          )}
          {reviews.length > 0 && (
            <section data-testid="deep-reviews" className="rounded border p-3 text-sm">
              <h3 className="font-medium">Deep mode reviews</h3>
              <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-muted-foreground">
                {reviews.map((review) => (
                  <li key={review.round}>
                    {review.verdict === "revise"
                      ? `Revised ${review.steps.join(", ") || "the analysis"}: ${review.reason}`
                      : `Accepted: ${review.reason}`}
                  </li>
                ))}
              </ol>
            </section>
          )}
          {assumptions.length > 0 && (
            <section data-testid="assumptions" className="rounded border p-3 text-sm">
              <h3 className="font-medium">Assumptions</h3>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
                {assumptions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}
          <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
            <span>Source version: {run.dataset_version_id ?? "Not available"}</span>
            <span>
              Definition: {run.definition_id ?? "Not available"}
              {provenance.definition_version ? ` · v${String(provenance.definition_version)}` : ""}
            </span>
            <span>Imported at: {String(provenance.imported_at ?? "Not available")}</span>
            <span>Freshness: {String(provenance.source_freshness ?? "Not available")}</span>
            <span>Privacy: {String(provenance.privacy_mode ?? "Not available")}</span>
            <span>
              AI model:{" "}
              {String(
                provenance.model ??
                  (run.definition_id ? "None (fixed calculations)" : "Not recorded"),
              )}
            </span>
            <span>
              Engine:{" "}
              {String(provenance.engine_version ?? provenance.engine_kind ?? "Not available")}
            </span>
          </div>
          {(evidence.length > 0 ||
            checks.length > 0 ||
            Object.keys(metricDefinitions).length > 0) && (
            <details className="rounded border p-3">
              <summary className="font-medium">Evidence and definitions</summary>
              <div className="mt-3 space-y-4 text-sm">
                {checks.map((check, index) => (
                  <div key={`${String(check.code)}-${index}`}>
                    <Badge variant={check.passed ? "secondary" : "destructive"}>
                      {check.passed ? "passed" : "failed"}
                    </Badge>{" "}
                    {String(check.message ?? check.code)}
                  </div>
                ))}
                {Object.entries(metricDefinitions).map(([name, definition]) => (
                  <p key={name}>
                    <strong>{name}:</strong> {String(definition)}
                  </p>
                ))}
                <div className="space-y-2">
                  {evidence.map((entry) => {
                    const value = entry.value === null ? "Not available" : String(entry.value);
                    return (
                      <div
                        key={String(entry.id)}
                        data-testid={`evidence-${String(entry.id)}`}
                        className="rounded bg-muted p-2"
                      >
                        <a
                          className="font-mono underline"
                          href={`#artifact-${run.id}-${String(entry.artifact)}`}
                        >
                          {String(entry.id)}
                        </a>{" "}
                        {entry.text ? <span>&ldquo;{String(entry.text)}&rdquo; = </span> : null}
                        <span>{value}</span>
                        <span className="ml-2 text-muted-foreground">
                          {String(entry.artifact)},{" "}
                          {entry.row !== null && entry.row !== undefined
                            ? `row ${String(entry.row)}, column ${String(entry.column)}`
                            : entry.kind === "column_total" || entry.kind === "column_average"
                              ? `${entry.kind === "column_total" ? "total" : "average"} of column ${String(entry.column)}`
                              : "row count"}
                        </span>
                      </div>
                    );
                  })}
                </div>
                <pre className="overflow-auto text-xs">
                  {JSON.stringify(
                    {
                      period: provenance.period,
                      joins: (provenance.definition as Record<string, unknown> | undefined)?.joins,
                      filters: (provenance.definition as Record<string, unknown> | undefined)
                        ?.filters,
                      sources: provenance.sources,
                    },
                    null,
                    2,
                  )}
                </pre>
              </div>
            </details>
          )}
          {trace.length > 0 && (
            <details className="rounded border p-3" data-testid="run-trace">
              <summary className="font-medium">Run trace</summary>
              <p className="mt-2 text-xs text-muted-foreground">
                Each model call, query, chart and check in order. Prompts and data values are not
                stored; &ldquo;shared&rdquo; says what the model was allowed to see.
              </p>
              <table className="mt-2 w-full text-left text-xs">
                <thead>
                  <tr className="text-muted-foreground">
                    <th className="py-1 pr-2 font-normal">Step</th>
                    <th className="py-1 pr-2 font-normal">Type</th>
                    <th className="py-1 pr-2 font-normal">Time</th>
                    <th className="py-1 font-normal">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {trace.map((event, index) => (
                    <tr key={`${event.step}-${index}`} className="border-t align-top">
                      <td className="py-1 pr-2 font-mono">{event.step}</td>
                      <td className="py-1 pr-2">
                        {event.kind}
                        {event.ok ? "" : " (failed)"}
                      </td>
                      <td className="py-1 pr-2">{(event.duration_ms / 1000).toFixed(2)} s</td>
                      <td className="py-1 break-words">
                        {Object.entries(event.details)
                          .filter(([, value]) => value !== null && value !== undefined)
                          .map(
                            ([key, value]) =>
                              `${key.replaceAll("_", " ")}: ${Array.isArray(value) ? value.join(", ") : String(value)}`,
                          )
                          .join(" · ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
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
