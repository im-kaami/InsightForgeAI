"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { AddDataDialog } from "@/components/add-data-dialog";
import { DataQuality } from "@/components/data-quality";
import { DatasetCleaning } from "@/components/dataset-cleaning";
import { DatasetNotes } from "@/components/dataset-notes";
import { DatasetRelationships } from "@/components/dataset-relationships";
import { ImportReview } from "@/components/import-review";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { datasets, health, type Dataset, type DatasetVersion } from "@/lib/api";

const descriptions = {
  local:
    "No LLM requests. Chat uses a generic local profiling plan; approved reports still calculate your saved metrics.",
  schema_only:
    "Sends table/column names, types and your current/prior questions. Dataset values, statistics and previous answer text stay local. Questions may contain data you type.",
  full: "Sends questions, schema context, result rows/statistics and conversation summaries. These may contain sensitive data.",
};

export function DatasetTrustPanel({
  dataset,
  onChanged,
}: {
  dataset: Dataset;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const versions = useQuery({
    queryKey: ["dataset-versions", dataset.id],
    queryFn: () => datasets.versions(dataset.id),
    enabled: dataset.kind !== "connection",
  });
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: health });
  const localModel = healthQuery.data?.local_model;
  const [mode, setMode] = useState<Dataset["llm_policy"]>(dataset.llm_policy);
  const [acknowledged, setAcknowledged] = useState(false);
  const [review, setReview] = useState<DatasetVersion | null>(null);
  const [busy, setBusy] = useState(false);

  async function savePrivacy() {
    setBusy(true);
    try {
      await datasets.setPrivacy(dataset.id, mode);
      await queryClient.invalidateQueries({ queryKey: ["dataset", dataset.id] });
      await queryClient.invalidateQueries({ queryKey: ["session-dataset", dataset.id] });
      setAcknowledged(false);
      onChanged();
      toast.success("Privacy setting saved");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save privacy setting");
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    setBusy(true);
    try {
      const result = await datasets.refresh(dataset.id);
      const values = await datasets.versions(dataset.id);
      await queryClient.invalidateQueries({ queryKey: ["dataset-versions", dataset.id] });
      const draft = values.find((item) => item.id === result.review_version_id);
      if (!draft) throw new Error("Refresh draft was not returned");
      setReview(draft);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not refresh source");
    } finally {
      setBusy(false);
    }
  }

  if (review) {
    return (
      <ImportReview
        datasetId={dataset.id}
        version={review}
        onBack={() => setReview(null)}
        onConfirmed={() => {
          setReview(null);
          void queryClient.invalidateQueries({ queryKey: ["dataset-versions", dataset.id] });
          onChanged();
        }}
      />
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Data sharing and privacy</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm">
            Current policy: <Badge variant="outline">{dataset.llm_policy}</Badge>
          </p>
          <div className="space-y-2">
            <Label htmlFor="privacy-mode">Data sharing</Label>
            <select
              id="privacy-mode"
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              value={mode}
              onChange={(event) => {
                setMode(event.target.value as Dataset["llm_policy"]);
                setAcknowledged(false);
              }}
            >
              <option value="local">Local only</option>
              <option value="schema_only">Schema-only cloud planning</option>
              <option value="full">Full cloud analysis</option>
            </select>
          </div>
          <p className="text-sm text-muted-foreground">
            {mode === "local" && localModel
              ? `Chat uses ${localModel} running on this computer, so nothing is sent to a cloud AI. Approved reports still calculate your saved metrics.`
              : descriptions[mode]}
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            I understand what this mode shares
          </label>
          <Button type="button" disabled={!acknowledged || busy} onClick={savePrivacy}>
            Save privacy setting
          </Button>
        </CardContent>
      </Card>
      {dataset.kind !== "connection" && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Version history</CardTitle>
            <div className="flex gap-2">
              {dataset.kind === "url" && (
                <Button type="button" variant="outline" disabled={busy} onClick={refresh}>
                  Refresh source
                </Button>
              )}
              <AddDataDialog
                datasetId={dataset.id}
                replaceVersion
                onComplete={() => {
                  void queryClient.invalidateQueries({
                    queryKey: ["dataset-versions", dataset.id],
                  });
                  onChanged();
                }}
              />
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {(versions.data ?? []).map((version) => (
              <div key={version.id} className="rounded-md border p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono">{version.id.slice(0, 8)}</span>
                  <Badge variant="outline">{version.state}</Badge>
                  {version.id === dataset.current_version_id && <Badge>current</Badge>}
                  {version.state === "draft" && (
                    <Button type="button" size="sm" onClick={() => setReview(version)}>
                      Review
                    </Button>
                  )}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {new Date(version.created_at).toLocaleString("en", { timeZone: "UTC" })} UTC
                </p>
                {version.sources.map((source, index) => (
                  <details key={`${version.id}-${index}`} className="mt-2">
                    <summary>{String(source.location)}</summary>
                    <p>SHA256: {String(source.sha256 ?? "Unavailable")}</p>
                  </details>
                ))}
              </div>
            ))}
          </CardContent>
        </Card>
      )}
      {dataset.kind !== "connection" && (
        <DatasetCleaning
          dataset={dataset}
          currentVersion={(versions.data ?? []).find(
            (item) => item.id === dataset.current_version_id,
          )}
          onDraft={setReview}
        />
      )}
      <DatasetNotes key={`${dataset.id}-${dataset.current_version_id}`} dataset={dataset} />
      <DatasetRelationships
        key={`rel-${dataset.id}-${dataset.current_version_id}`}
        dataset={dataset}
      />
      <DataQuality
        profile={dataset.profile}
        datasetId={dataset.id}
        versionId={dataset.current_version_id}
        onRefreshed={() => {
          void queryClient.invalidateQueries({ queryKey: ["dataset", dataset.id] });
          void queryClient.invalidateQueries({ queryKey: ["dataset-versions", dataset.id] });
          onChanged();
        }}
      />
    </div>
  );
}
