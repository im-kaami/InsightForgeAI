"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { connections, datasets, type Dataset } from "@/lib/api";

export function AddDataDialog({
  datasetId,
  onComplete,
}: {
  datasetId?: string;
  onComplete?: (dataset: Dataset) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [uri, setUri] = useState("");
  const [connectionName, setConnectionName] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [tables, setTables] = useState("");
  const existing = useQuery({
    queryKey: ["connections"],
    queryFn: connections.list,
    enabled: open && !datasetId,
  });
  async function execute(action: () => Promise<Dataset>) {
    setBusy(true);
    try {
      const result = await action();
      toast.success(datasetId ? "Source added" : "Dataset added");
      onComplete?.(result);
      setOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not add data");
    } finally {
      setBusy(false);
    }
  }
  const addUrl = () =>
    execute(() =>
      datasetId ? datasets.addSource(datasetId, url) : datasets.fromUrl(url, name || undefined),
    );
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button />}>{datasetId ? "Add source" : "Add data"}</DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{datasetId ? "Add a source" : "Add data"}</DialogTitle>
          <DialogDescription>
            Load files, public links, Google Sheets, or a database.
          </DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="upload">
          <TabsList className="grid grid-cols-4">
            <TabsTrigger value="upload">Upload files</TabsTrigger>
            <TabsTrigger value="url">From URL</TabsTrigger>
            <TabsTrigger value="sheet">Google Sheets</TabsTrigger>
            <TabsTrigger value="db" disabled={Boolean(datasetId)}>
              Database
            </TabsTrigger>
          </TabsList>
          <TabsContent value="upload" className="space-y-4">
            <div>
              <Label>Files</Label>
              <Input
                type="file"
                multiple
                accept=".csv,.tsv,.parquet,.json,.jsonl,.xlsx,.xls"
                onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
              />
            </div>
            {!datasetId && (
              <div>
                <Label>Dataset name (optional)</Label>
                <Input value={name} onChange={(event) => setName(event.target.value)} />
              </div>
            )}
            <Button
              disabled={!files.length || busy}
              onClick={() =>
                execute(() =>
                  datasetId
                    ? datasets.addSource(datasetId, files[0])
                    : datasets.upload(files, name || undefined),
                )
              }
            >
              Upload
            </Button>
          </TabsContent>
          <TabsContent value="url" className="space-y-4">
            <Label>Data URL</Label>
            <Input
              placeholder="https://example.com/data.csv"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Direct CSV, Excel, Parquet, and JSON links are supported.
            </p>
            {!datasetId && (
              <Input
                placeholder="Dataset name (optional)"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            )}
            <Button disabled={!url || busy} onClick={addUrl}>
              Load URL
            </Button>
          </TabsContent>
          <TabsContent value="sheet" className="space-y-4">
            <Label>Google Sheets share link</Label>
            <Input value={url} onChange={(event) => setUrl(event.target.value)} />
            <p className="text-xs text-muted-foreground">
              Share the sheet as “Anyone with the link” before loading.
            </p>
            <Button disabled={!url || busy} onClick={addUrl}>
              Load sheet
            </Button>
          </TabsContent>
          <TabsContent value="db" className="space-y-4">
            <Label>Existing connection</Label>
            <Select value={connectionId} onValueChange={(value) => setConnectionId(value ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Choose a connection" />
              </SelectTrigger>
              <SelectContent>
                {existing.data?.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="text-center text-xs text-muted-foreground">
              or enter a new connection
            </div>
            <Input
              placeholder="Connection name"
              value={connectionName}
              onChange={(event) => setConnectionName(event.target.value)}
            />
            <Input
              placeholder="postgresql://, mysql://, or sqlite:///"
              value={uri}
              onChange={(event) => setUri(event.target.value)}
            />
            <Input
              placeholder="Optional tables: orders, customers"
              value={tables}
              onChange={(event) => setTables(event.target.value)}
            />
            <Button
              disabled={(!connectionId && !uri) || busy}
              onClick={() =>
                execute(() =>
                  datasets.fromConnection({
                    connection_id: connectionId || undefined,
                    uri: uri || undefined,
                    name: name || connectionName || "Connected data",
                    tables: tables ? tables.split(",").map((value) => value.trim()) : undefined,
                  }),
                )
              }
            >
              Connect
            </Button>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
