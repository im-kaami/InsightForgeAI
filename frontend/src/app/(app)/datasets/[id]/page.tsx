"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { AddDataDialog } from "@/components/add-data-dialog";
import { DataTable } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
  const query = useQuery({ queryKey: ["dataset", id], queryFn: () => datasets.get(id) });
  const tables = (query.data?.schema as { tables?: SchemaTable[] })?.tables ?? [];
  const [selected, setSelected] = useState<string | null>(null);
  const active = selected ?? tables[0]?.name;
  const table = tables.find((item) => item.name === active);
  const preview = useQuery({
    queryKey: ["preview", id, active],
    queryFn: () => datasets.preview(id, active!),
    enabled: Boolean(active),
  });
  if (!query.data) return <p className="text-muted-foreground">Loading dataset...</p>;
  const dataset = query.data;
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
            onComplete={() => client.invalidateQueries({ queryKey: ["dataset", id] })}
          />
          <Button
            onClick={async () =>
              router.push(`/sessions/${(await sessions.create(id, dataset.name)).id}`)
            }
          >
            Start analysis
          </Button>
        </div>
      </header>
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
                      Sample values of columns flagged as sensitive are never sent to the LLM.
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
    </div>
  );
}
