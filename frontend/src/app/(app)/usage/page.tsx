"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
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
import { usage } from "@/lib/api";
import { formatUsd } from "@/lib/utils";

const PERIODS = [7, 30, 90];

export default function UsagePage() {
  const [days, setDays] = useState(30);
  const query = useQuery({ queryKey: ["usage", days], queryFn: () => usage(days) });
  const data = query.data;
  const totals = data?.totals;
  const priced = totals ? totals.runs - totals.unpriced_runs : 0;
  const cost = (value: number, unpriced: number, runs: number) =>
    unpriced === runs ? "price not set" : formatUsd(value);
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold">Usage</h1>
          <p className="text-muted-foreground">
            AI-answered runs by model and by day. Runs that used no AI model are not listed.
          </p>
        </div>
        <div className="flex gap-2" role="group" aria-label="Period">
          {PERIODS.map((value) => (
            <Button
              key={value}
              variant={days === value ? "default" : "outline"}
              aria-pressed={days === value}
              onClick={() => setDays(value)}
            >
              {value} days
            </Button>
          ))}
        </div>
      </header>
      {query.isError && (
        <p role="alert" className="text-destructive">
          {query.error instanceof Error ? query.error.message : "Could not load usage"}
        </p>
      )}
      {!data && !query.isError && <p className="text-muted-foreground">Loading usage...</p>}
      {data && totals && totals.runs === 0 && (
        <p className="text-muted-foreground" data-testid="usage-empty">
          No AI-answered runs in this period.
        </p>
      )}
      {data && totals && totals.runs > 0 && (
        <>
          <div className="grid gap-4 sm:grid-cols-3" data-testid="usage-totals">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">Runs</CardTitle>
              </CardHeader>
              <CardContent className="text-2xl font-semibold">
                {totals.runs.toLocaleString()}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">Tokens</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">
                  {(totals.prompt_tokens + totals.completion_tokens).toLocaleString()}
                </p>
                <p className="text-xs text-muted-foreground">
                  {totals.prompt_tokens.toLocaleString()} prompt ·{" "}
                  {totals.completion_tokens.toLocaleString()} completion
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Estimated cost
                </CardTitle>
              </CardHeader>
              <CardContent className="text-2xl font-semibold">
                {priced > 0 ? formatUsd(totals.cost_usd) : "—"}
              </CardContent>
            </Card>
          </div>
          {totals.unpriced_runs > 0 && (
            <p role="note" className="rounded-md border border-amber-500/30 p-3 text-sm">
              {totals.unpriced_runs} {totals.unpriced_runs === 1 ? "run" : "runs"} used a model
              without a configured price; set LLM_PRICES in backend/.env to estimate their cost.
            </p>
          )}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">By model</CardTitle>
            </CardHeader>
            <CardContent>
              <Table data-testid="usage-by-model">
                <TableHeader>
                  <TableRow>
                    <TableHead>Provider</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Runs</TableHead>
                    <TableHead>Prompt tokens</TableHead>
                    <TableHead>Completion tokens</TableHead>
                    <TableHead>Estimated cost</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.by_model.map((row) => (
                    <TableRow key={`${row.provider}:${row.model}`}>
                      <TableCell>{row.provider}</TableCell>
                      <TableCell className="font-medium">{row.model}</TableCell>
                      <TableCell>{row.runs.toLocaleString()}</TableCell>
                      <TableCell>{row.prompt_tokens.toLocaleString()}</TableCell>
                      <TableCell>{row.completion_tokens.toLocaleString()}</TableCell>
                      <TableCell>{cost(row.cost_usd, row.unpriced_runs, row.runs)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">By day (UTC)</CardTitle>
            </CardHeader>
            <CardContent>
              <Table data-testid="usage-by-day">
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Runs</TableHead>
                    <TableHead>Prompt tokens</TableHead>
                    <TableHead>Completion tokens</TableHead>
                    <TableHead>Estimated cost</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.by_day.map((row) => (
                    <TableRow key={row.date}>
                      <TableCell>{row.date}</TableCell>
                      <TableCell>{row.runs.toLocaleString()}</TableCell>
                      <TableCell>{row.prompt_tokens.toLocaleString()}</TableCell>
                      <TableCell>{row.completion_tokens.toLocaleString()}</TableCell>
                      <TableCell>{cost(row.cost_usd, row.unpriced_runs, row.runs)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
      <p className="text-xs text-muted-foreground">
        Estimates from token counts reported by the provider and the prices you configured; your
        provider&apos;s bill is authoritative.
      </p>
    </div>
  );
}
