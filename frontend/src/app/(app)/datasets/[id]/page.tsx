"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { toast } from "sonner";
import { AddDataDialog } from "@/components/add-data-dialog";
import { DataTable } from "@/components/data-table";
import { DatasetTrustPanel } from "@/components/dataset-trust-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VerifiedReportBuilder } from "@/components/verified-report-builder";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { datasets, sessions } from "@/lib/api";
import { cn } from "@/lib/utils";

type SchemaTable = {
  name: string;
  row_count: number;
  columns: {
    name: string;
    dtype: string;
    sample_values?: string[];
    sensitivity?: string | null;
  }[];
};
export default function DatasetPage() {
  const id = String(useParams().id);
  const router = useRouter();
  const client = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [isNavigating, startNavigation] = useTransition();
  const requestInFlight = useRef(false);
  const query = useQuery({ queryKey: ["dataset", id], queryFn: () => datasets.get(id) });
  const versions = useQuery({
    queryKey: ["dataset-versions", id],
    queryFn: () => datasets.versions(id),
    enabled: query.data?.kind !== "connection" && Boolean(query.data),
  });
  const tables = (query.data?.schema as { tables?: SchemaTable[] })?.tables ?? [];
  const [selected, setSelected] = useState<string | null>(null);
  const active =
    selected && tables.some((item) => item.name === selected) ? selected : tables[0]?.name;
  const table = tables.find((item) => item.name === active);
  const preview = useQuery({
    queryKey: ["preview", id, query.data?.current_version_id, active],
    queryFn: () => datasets.preview(id, active!, query.data?.current_version_id ?? undefined),
    enabled: Boolean(active),
  });
  if (query.isError)
    return (
      <p role="alert" className="text-destructive">
        {query.error instanceof Error ? query.error.message : "Could not load dataset"}
      </p>
    );
  if (!query.data) return <p className="text-muted-foreground">Loading dataset...</p>;
  const dataset = query.data;
  const canAnalyze = dataset.kind === "connection" || Boolean(dataset.current_version_id);
  async function startAnalysis() {
    if (requestInFlight.current || creating || isNavigating) return;
    requestInFlight.current = true;
    setCreating(true);
    try {
      const session = await sessions.create(id, dataset.name);
      startNavigation(() => router.push(`/sessions/${session.id}`));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not start analysis");
    } finally {
      requestInFlight.current = false;
      setCreating(false);
    }
  }
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold">{dataset.name}</h1>
          <p className="text-muted-foreground">
            {dataset.kind} · {dataset.tables.length} tables
          </p>
        </div>
        <div className="flex gap-2">
          <AddDataDialog
            datasetId={id}
            onComplete={() => {
              void client.invalidateQueries({ queryKey: ["dataset", id] });
              void client.invalidateQueries({ queryKey: ["dataset-versions", id] });
            }}
          />
          <Button disabled={!canAnalyze || creating || isNavigating} onClick={startAnalysis}>
            {creating || isNavigating ? "Opening..." : "Start analysis"}
          </Button>
        </div>
      </header>
      {!canAnalyze && (
        <div className="rounded-md border border-amber-500/30 p-3 text-sm">
          Review and confirm the imported version before starting analysis.
        </div>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Sources</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {dataset.sources.map((source, index) => (
            <div key={index} className="rounded border p-3">
              <span className="font-medium">{String(source.kind)}</span>
              <span className="ml-3 text-muted-foreground">{String(source.location)}</span>
            </div>
          ))}
        </CardContent>
      </Card>
      <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Tables</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {tables.map((item) => (
              <button
                key={item.name}
                className={cn(
                  "flex w-full justify-between rounded px-3 py-2 text-left text-sm",
                  active === item.name ? "bg-primary text-primary-foreground" : "hover:bg-muted",
                )}
                onClick={() => setSelected(item.name)}
              >
                <span className="truncate">{item.name}</span>
                <span>{item.row_count}</span>
              </button>
            ))}
          </CardContent>
        </Card>
        <Card>
          {table && (
            <>
              <CardHeader>
                <CardTitle>{table.name}</CardTitle>
              </CardHeader>
              <CardContent>
                <Tabs defaultValue="columns">
                  <TabsList>
                    <TabsTrigger value="columns">Columns</TabsTrigger>
                    <TabsTrigger value="preview">Preview</TabsTrigger>
                  </TabsList>
                  <TabsContent value="columns" className="space-y-3">
                    <p className="text-xs text-muted-foreground">
                      Sensitive sample labels are heuristic. The selected data-sharing policy
                      controls outbound requests.
                    </p>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Name</TableHead>
                          <TableHead>Type</TableHead>
                          <TableHead>Sample values</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {table.columns.map((column) => (
                          <TableRow key={column.name}>
                            <TableCell className="font-medium">
                              <span className="flex items-center gap-2">
                                {column.name}
                                {column.sensitivity && (
                                  <Badge variant="outline">{column.sensitivity}</Badge>
                                )}
                              </span>
                            </TableCell>
                            <TableCell>{column.dtype}</TableCell>
                            <TableCell className="text-muted-foreground">
                              {column.sample_values?.join(", ")}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TabsContent>
                  <TabsContent value="preview">
                    {preview.data && (
                      <DataTable columns={preview.data.columns} rows={preview.data.rows} />
                    )}
                  </TabsContent>
                </Tabs>
              </CardContent>
            </>
          )}
        </Card>
      </div>
      <DatasetTrustPanel
        key={dataset.id}
        dataset={dataset}
        onChanged={() => {
          void client.invalidateQueries({ queryKey: ["dataset", id] });
          void client.invalidateQueries({ queryKey: ["dataset-versions", id] });
        }}
      />
      <VerifiedReportBuilder
        key={`${dataset.id}:${dataset.current_version_id ?? "draft"}`}
        dataset={dataset}
        versions={versions.data ?? []}
      />
    </div>
  );
}
