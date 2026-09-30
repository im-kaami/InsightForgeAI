"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { models } from "@/lib/api";

type Effect = { name: string; value: number; magnitude: string };
type Interval = { label: string; low: number; high: number; level: number };
export type StatResult = {
  name: string;
  test: string;
  n: number;
  method?: string;
  y?: string;
  p_value?: number | null;
  p_adjusted?: number | null;
  statistic?: number | null;
  effect_size?: Effect | null;
  interval?: Interval | null;
  groups?: Record<string, unknown>[];
  pairwise?: {
    a: string;
    b: string;
    difference: number;
    p_value: number;
    p_adjusted: number;
    significant: boolean;
  }[];
  checks?: string[];
  cautions?: string[];
  robustness?: { analysis: string; p_value: number; holds: boolean }[];
  importance?: { feature: string; importance: number; spread: number }[];
  interpretation: string;
  note?: string | null;
};

const number = (value: unknown) =>
  typeof value === "number"
    ? value.toLocaleString(undefined, { maximumSignificantDigits: 4 })
    : String(value ?? "");
const STATISTIC_LABELS: Record<string, string> = {
  explain_change: "Total change",
  forecast: "Next period forecast",
  anomalies: "Unusual periods",
  segments: "Groups found",
  predict: "Held-out score",
};
const pText = (value: number) => (value < 0.001 ? "< 0.001" : `= ${value.toFixed(3)}`);

function SaveModel({
  runId,
  position,
  target,
}: {
  runId: string;
  position: number;
  target: string;
}) {
  const [name, setName] = useState(`${target} model`);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const fieldId = `model-name-${runId}-${position}`;
  async function save() {
    setBusy(true);
    try {
      await models.save(runId, position, name.trim());
      setSaved(true);
      toast.success("Model saved. Score new data from the dataset page.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save the model");
    } finally {
      setBusy(false);
    }
  }
  if (saved)
    return (
      <p data-testid="model-saved" className="text-xs text-muted-foreground">
        Saved as “{name.trim()}”. Open the dataset page to score new data and check drift.
      </p>
    );
  return (
    <div data-testid="save-model" className="flex flex-wrap items-end gap-2">
      <div className="space-y-1">
        <Label htmlFor={fieldId} className="text-xs">
          Model name
        </Label>
        <Input
          id={fieldId}
          className="h-8 w-56"
          maxLength={200}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <Button size="sm" variant="outline" disabled={busy || !name.trim()} onClick={save}>
        {busy ? "Saving..." : "Save model"}
      </Button>
    </div>
  );
}

export function StatResultCard({
  result,
  runId,
  position,
}: {
  result: StatResult;
  runId?: string;
  position?: number;
}) {
  const groups = result.groups ?? [];
  const columns = groups.length ? Object.keys(groups[0]) : [];
  const p = result.p_adjusted ?? result.p_value;
  return (
    <section data-testid="stat-result" className="space-y-3 rounded-md border p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">{result.name}</h3>
        <Badge variant="outline">Tested method: {result.test}</Badge>
        {p != null && (
          <Badge variant={p < 0.05 ? "default" : "secondary"}>
            {p < 0.05 ? "Unlikely to be chance" : "Could be chance"}
          </Badge>
        )}
      </div>
      <p>{result.interpretation}</p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-muted-foreground md:grid-cols-4">
        {result.p_value != null ? (
          <div>
            <dt className="text-xs">p-value</dt>
            <dd className="text-foreground">p {pText(result.p_value)}</dd>
          </div>
        ) : (
          result.statistic != null && (
            <div>
              <dt className="text-xs">{STATISTIC_LABELS[result.method ?? ""] ?? "Result"}</dt>
              <dd className="text-foreground">{number(result.statistic)}</dd>
            </div>
          )
        )}
        {result.p_adjusted != null && (
          <div>
            <dt className="text-xs">Adjusted for several tests</dt>
            <dd className="text-foreground">p {pText(result.p_adjusted)}</dd>
          </div>
        )}
        {result.effect_size && (
          <div>
            <dt className="text-xs">Effect size ({result.effect_size.name})</dt>
            <dd className="text-foreground">
              {number(result.effect_size.value)} · {result.effect_size.magnitude}
            </dd>
          </div>
        )}
        {result.interval && (
          <div>
            <dt className="text-xs">95% CI: {result.interval.label}</dt>
            <dd className="text-foreground">
              {number(result.interval.low)} to {number(result.interval.high)}
            </dd>
          </div>
        )}
        <div>
          <dt className="text-xs">Rows used</dt>
          <dd className="text-foreground">{result.n.toLocaleString()}</dd>
        </div>
      </dl>
      {columns.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column} className="py-1 pr-3 font-normal text-muted-foreground">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map((row, index) => (
                <tr key={index} className="border-t">
                  {columns.map((column) => (
                    <td key={column} className="py-1 pr-3">
                      {number(row[column])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(result.pairwise ?? []).length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">
            Pairwise comparisons (Holm-adjusted)
          </summary>
          <ul className="mt-1 space-y-0.5 text-xs">
            {(result.pairwise ?? []).map((pair) => (
              <li key={`${pair.a}-${pair.b}`}>
                {pair.a} vs {pair.b}: difference {number(pair.difference)}, adjusted p{" "}
                {pText(pair.p_adjusted)}
                {pair.significant ? " (significant)" : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
      {(result.importance ?? []).length > 0 && (
        <div data-testid="importance" className="text-xs">
          <p className="font-medium">What the model relies on (permutation importance)</p>
          <ul className="mt-0.5 space-y-0.5">
            {(result.importance ?? []).map((row) => (
              <li key={row.feature}>
                {row.feature}: {number(row.importance)} (± {number(row.spread)})
              </li>
            ))}
          </ul>
        </div>
      )}
      {(result.robustness ?? []).length > 0 && (
        <div data-testid="robustness" className="text-xs">
          <p className="font-medium">Robustness check</p>
          <ul className="mt-0.5 space-y-0.5">
            {(result.robustness ?? []).map((row) => (
              <li key={row.analysis}>
                {row.holds ? "Holds" : "Changes"}: {row.analysis} (p {pText(row.p_value)})
              </li>
            ))}
          </ul>
        </div>
      )}
      <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
        {(result.checks ?? []).map((check) => (
          <li key={check}>{check}</li>
        ))}
        {(result.cautions ?? []).map((caution) => (
          <li key={caution} className="text-amber-700 dark:text-amber-400">
            {caution}
          </li>
        ))}
        {result.note && <li>{result.note}</li>}
      </ul>
      {result.method === "predict" && runId && position != null && (
        <SaveModel runId={runId} position={position} target={String(result.y ?? "target")} />
      )}
    </section>
  );
}
