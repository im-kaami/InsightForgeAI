"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { DataTable } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  models,
  type Dataset,
  type DatasetVersion,
  type ModelScore,
  type ModelScoring,
  type SavedModel,
} from "@/lib/api";

const METRIC_LABELS: Record<string, string> = {
  balanced_accuracy: "Balanced accuracy",
  roc_auc: "ROC AUC",
  mae: "Typical error (MAE)",
  r_squared: "R-squared",
};
const METRIC_ORDER = ["balanced_accuracy", "roc_auc", "mae", "r_squared"];

const number = (value: unknown) =>
  typeof value === "number"
    ? value.toLocaleString(undefined, { maximumSignificantDigits: 3 })
    : "Not available";

const bandVariant = (band: string) =>
  band === "major shift" ? "destructive" : band === "moderate shift" ? "secondary" : "outline";

function ScoreResult({ model, score }: { model: SavedModel; score: ModelScore }) {
  const retrain = score.recommendation.verdict === "retrain recommended";
  const metricKeys = METRIC_ORDER.filter(
    (key) => key in (score.holdout_metrics ?? {}) || key in (score.new_data_metrics ?? {}),
  );
  return (
    <div data-testid="score-result" className="space-y-3 border-t pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={retrain ? "destructive" : "outline"}>
          {retrain ? "Retraining recommended" : "No retraining signal"}
        </Badge>
        <span className="text-muted-foreground">
          {score.rows_scored.toLocaleString()} rows scored on version{" "}
          <span className="font-mono">{score.version_id.slice(0, 8)}</span>
        </span>
      </div>
      <p>{score.recommendation.detail}</p>
      {score.recommendation.reasons.length > 0 && (
        <ul className="list-disc pl-5 text-xs">
          {score.recommendation.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {metricKeys.length > 0 && (
        <table data-testid="accuracy-comparison" className="text-left text-xs">
          <thead>
            <tr className="text-muted-foreground">
              <th className="py-1 pr-4 font-normal">Measure</th>
              <th className="py-1 pr-4 font-normal">When trained (held-out rows)</th>
              <th className="py-1 pr-4 font-normal">On this data</th>
            </tr>
          </thead>
          <tbody>
            {metricKeys.map((key) => (
              <tr key={key} className="border-t">
                <td className="py-1 pr-4">{METRIC_LABELS[key]}</td>
                <td className="py-1 pr-4">{number(score.holdout_metrics?.[key])}</td>
                <td className="py-1 pr-4">{number(score.new_data_metrics?.[key])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {score.new_data_metrics == null && (
        <p className="text-xs text-muted-foreground">
          This data has no known {model.target} values, so accuracy on it cannot be measured.
        </p>
      )}
      <div>
        <p className="text-xs font-medium">
          Drift per feature (population stability index; below 0.1 stable, above 0.25 major)
        </p>
        <table data-testid="drift-table" className="mt-1 w-full text-left text-xs">
          <thead>
            <tr className="text-muted-foreground">
              <th className="py-1 pr-3 font-normal">Feature</th>
              <th className="py-1 pr-3 font-normal">PSI</th>
              <th className="py-1 pr-3 font-normal">Shift</th>
              <th className="py-1 pr-3 font-normal">Values not seen in training</th>
              <th className="py-1 pr-3 font-normal">Change in missing values</th>
            </tr>
          </thead>
          <tbody>
            {score.drift.features.map((row) => (
              <tr key={row.feature} className="border-t">
                <td className="py-1 pr-3">{row.feature}</td>
                <td className="py-1 pr-3">{row.psi.toFixed(3)}</td>
                <td className="py-1 pr-3">
                  <Badge variant={bandVariant(row.band)}>{row.band}</Badge>
                </td>
                <td className="py-1 pr-3">{(row.unseen_share * 100).toFixed(1)}%</td>
                <td className="py-1 pr-3">
                  {row.missing_change >= 0 ? "+" : ""}
                  {(row.missing_change * 100).toFixed(1)} pts
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        Drift compares the new data with the training data; it does not prove the model is right or
        wrong.
      </p>
      <DataTable
        columns={score.preview_columns}
        rows={score.preview_rows}
        totalRows={score.rows_scored}
      />
      <Button
        size="sm"
        variant="outline"
        onClick={() =>
          models
            .downloadScores(model.id, model.name)
            .catch((error: unknown) =>
              toast.error(error instanceof Error ? error.message : "Could not download scores"),
            )
        }
      >
        Download scores (CSV)
      </Button>
    </div>
  );
}

function SavedModelItem({
  model,
  versions,
  currentVersionId,
}: {
  model: SavedModel;
  versions: DatasetVersion[];
  currentVersionId: string | null | undefined;
}) {
  const client = useQueryClient();
  const ready = versions.filter((version) => version.state === "ready");
  const [versionId, setVersionId] = useState<string>(
    currentVersionId ?? ready[0]?.id ?? model.dataset_version_id,
  );
  const [score, setScore] = useState<ModelScore | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fieldId = `score-version-${model.id}`;
  const metrics = model.metrics as Record<string, number>;

  async function runScore() {
    setBusy(true);
    try {
      setScore(await models.score(model.id, versionId));
      await client.invalidateQueries({ queryKey: ["model-scorings", model.id] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not score the data");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await models.remove(model.id);
      toast.success("Model deleted");
      await client.invalidateQueries({ queryKey: ["models", model.dataset_id] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not delete the model");
      setBusy(false);
    }
  }

  return (
    <li data-testid="saved-model" className="space-y-3 rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{model.name}</span>
        <Badge variant="outline">{model.task}</Badge>
        <Badge variant="outline">Tested method: {model.model_type}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        Predicts {model.target} from {model.features.join(", ")}. Trained on version{" "}
        <span className="font-mono">{model.dataset_version_id.slice(0, 8)}</span>
        {model.created_at
          ? ` on ${new Date(model.created_at).toLocaleString("en", { timeZone: "UTC" })} UTC`
          : ""}
        .
        {METRIC_ORDER.filter((key) => key in metrics).map(
          (key) => ` ${METRIC_LABELS[key]} ${number(metrics[key])}.`,
        )}
      </p>
      {model.warning && (
        <p className="text-xs text-amber-700 dark:text-amber-400">{model.warning}</p>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor={fieldId} className="text-xs">
            Version to score
          </Label>
          <select
            id={fieldId}
            className="h-8 rounded-md border bg-background px-2 text-sm"
            value={versionId}
            onChange={(event) => setVersionId(event.target.value)}
          >
            {ready.map((version) => (
              <option key={version.id} value={version.id}>
                {version.id.slice(0, 8)}
                {version.id === currentVersionId ? " (current)" : ""}
                {version.id === model.dataset_version_id ? " (training data)" : ""}
              </option>
            ))}
          </select>
        </div>
        <Button size="sm" disabled={busy || !versionId} onClick={runScore}>
          {busy && !confirmDelete ? "Scoring..." : "Score and check drift"}
        </Button>
        {confirmDelete ? (
          <>
            <Button size="sm" variant="destructive" disabled={busy} onClick={remove}>
              Confirm delete
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)}>
            Delete model
          </Button>
        )}
      </div>
      {score && <ScoreResult model={model} score={score} />}
      <ModelMonitoring model={model} />
    </li>
  );
}

const PRESETS = [
  ["Daily 06:00", "0 6 * * *"],
  ["Weekly Mon 06:00", "0 6 * * 1"],
  ["Monthly 1st 06:00", "0 6 1 * *"],
] as const;

const when = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString() : "Not scheduled";

function describeScoring(item: ModelScoring) {
  if (item.status === "failed") return `Check failed: ${item.error ?? "unknown error"}`;
  const retrain = item.verdict === "retrain recommended";
  const psi = item.max_psi == null ? "" : `, largest drift PSI ${item.max_psi.toFixed(3)}`;
  return `${retrain ? "Retraining recommended" : "No retraining signal"} (${(
    item.rows_scored ?? 0
  ).toLocaleString()} rows${psi})`;
}

function ModelMonitoring({ model }: { model: SavedModel }) {
  const client = useQueryClient();
  const schedule = model.schedule;
  const [cron, setCron] = useState(schedule?.cron ?? "0 6 * * 1");
  const [timezone, setTimezone] = useState(
    () => schedule?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
  );
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const history = useQuery({
    queryKey: ["model-scorings", model.id],
    queryFn: () => models.scorings(model.id),
  });
  const cronId = `model-cron-${model.id}`;
  const zoneId = `model-zone-${model.id}`;

  async function act(action: () => Promise<unknown>, done?: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await action();
      await client.invalidateQueries({ queryKey: ["models", model.dataset_id] });
      await client.invalidateQueries({ queryKey: ["model-scorings", model.id] });
      await client.invalidateQueries({ queryKey: ["model-alerts"] });
      if (done) toast.success(done);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Something went wrong");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const items = history.data ?? [];
  const alerts = items.filter((item) => item.alert && !item.acknowledged_at);
  return (
    <div data-testid="model-monitoring" className="space-y-2">
      {alerts.map((item) => (
        <div
          key={item.id}
          role="alert"
          data-testid="model-alert"
          className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 p-2"
        >
          <span className="flex-1">
            {new Date(item.created_at ?? "").toLocaleString()}: {describeScoring(item)}
            {item.reasons?.length ? ` because ${item.reasons.join("; ")}` : ""}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => act(() => models.acknowledge(model.id, item.id), "Alert dismissed")}
          >
            Dismiss
          </Button>
        </div>
      ))}
      <details className="rounded-md border p-3">
        <summary className="cursor-pointer font-medium">
          Monitoring{" "}
          {schedule?.enabled ? (
            <Badge variant="outline">Next check {when(schedule.next_run_at)}</Badge>
          ) : (
            <Badge variant="secondary">No schedule</Badge>
          )}{" "}
          {model.open_alerts > 0 && (
            <Badge variant="destructive">
              {model.open_alerts} alert{model.open_alerts === 1 ? "" : "s"}
            </Badge>
          )}
        </summary>
        <div className="mt-3 space-y-3">
          <p className="text-xs text-muted-foreground">
            A scheduled check scores the dataset&apos;s current version with this model and compares
            it with the training data. It raises an alert when retraining is recommended or the
            check fails (for example a missing column or a blocking rule). Each check replaces the
            latest scores CSV. Checks run only while the InsightForge backend is running.
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor={cronId} className="text-xs">
                Schedule (cron)
              </Label>
              <Input
                id={cronId}
                className="h-8 w-40 font-mono"
                value={cron}
                onChange={(event) => setCron(event.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={zoneId} className="text-xs">
                Time zone
              </Label>
              <Input
                id={zoneId}
                className="h-8 w-44"
                value={timezone}
                onChange={(event) => setTimezone(event.target.value)}
              />
            </div>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) => setEnabled(event.target.checked)}
              />
              Enabled
            </label>
            <Button
              size="sm"
              disabled={busy || !cron.trim()}
              onClick={() =>
                act(
                  () => models.saveSchedule(model.id, { cron: cron.trim(), timezone, enabled }),
                  "Schedule saved",
                )
              }
            >
              Save schedule
            </Button>
            {schedule && (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => act(() => models.runScheduleNow(model.id), "Check finished")}
                >
                  {busy ? "Checking..." : "Check now"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => act(() => models.removeSchedule(model.id), "Schedule removed")}
                >
                  Remove schedule
                </Button>
              </>
            )}
          </div>
          <div className="flex flex-wrap gap-1">
            {PRESETS.map(([label, value]) => (
              <Button key={value} size="sm" variant="ghost" onClick={() => setCron(value)}>
                {label}
              </Button>
            ))}
          </div>
          <div>
            <p className="text-xs font-medium">Recent checks</p>
            {items.length === 0 ? (
              <p className="text-xs text-muted-foreground">No checks yet.</p>
            ) : (
              <ul className="mt-1 space-y-0.5 text-xs" data-testid="scoring-history">
                {items.slice(0, 10).map((item) => (
                  <li key={item.id}>
                    {new Date(item.created_at ?? "").toLocaleString()} ·{" "}
                    {item.trigger === "scheduled" ? "scheduled" : "by hand"} ·{" "}
                    {describeScoring(item)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </details>
    </div>
  );
}

export function SavedModels({
  dataset,
  versions,
}: {
  dataset: Dataset;
  versions: DatasetVersion[];
}) {
  const list = useQuery({
    queryKey: ["models", dataset.id],
    queryFn: () => models.list(dataset.id),
  });
  return (
    <Card data-testid="saved-models">
      <CardHeader>
        <CardTitle>Saved models</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {list.isError && (
          <p role="alert" className="text-destructive">
            {list.error instanceof Error ? list.error.message : "Could not load saved models"}
          </p>
        )}
        {list.data && list.data.length === 0 && (
          <p className="text-muted-foreground">
            No saved models yet. Ask a question such as “What predicts churn?” and choose Save model
            on the prediction result.
          </p>
        )}
        {list.data && list.data.length > 0 && (
          <ul className="space-y-3">
            {list.data.map((model) => (
              <SavedModelItem
                key={model.id}
                model={model}
                versions={versions}
                currentVersionId={dataset.current_version_id}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
