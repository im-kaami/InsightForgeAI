"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { ImportReview } from "@/components/import-review";
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
import {
  connections,
  datasets,
  type Dataset,
  type DatasetVersion,
  type ImportOptions,
} from "@/lib/api";

type EditableOptions = {
  table_name: string;
  header_row: number;
  text_columns: string;
  sheets: string;
};

const blankOptions = (): EditableOptions => ({
  table_name: "",
  header_row: 1,
  text_columns: "",
  sheets: "",
});

export function AddDataDialog({
  datasetId,
  replaceVersion = false,
  onComplete,
}: {
  datasetId?: string;
  replaceVersion?: boolean;
  onComplete?: (dataset: Dataset) => void;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [fileOptions, setFileOptions] = useState<EditableOptions[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [uri, setUri] = useState("");
  const [connectionName, setConnectionName] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [tables, setTables] = useState("");
  const [reviewDatasetId, setReviewDatasetId] = useState<string | null>(null);
  const [reviewVersion, setReviewVersion] = useState<DatasetVersion | null>(null);
  const existing = useQuery({
    queryKey: ["connections"],
    queryFn: connections.list,
    enabled: open && !datasetId,
  });

  function resetAndClose() {
    const affectedDatasetId = datasetId ?? reviewDatasetId;
    setFiles([]);
    setFileOptions([]);
    setName("");
    setUrl("");
    setUri("");
    setConnectionName("");
    setConnectionId("");
    setTables("");
    setReviewDatasetId(null);
    setReviewVersion(null);
    setOpen(false);
    void queryClient.invalidateQueries({ queryKey: ["datasets"] });
    if (affectedDatasetId) {
      void queryClient.invalidateQueries({
        queryKey: ["dataset-versions", affectedDatasetId],
      });
    }
  }

  function optionsPayload(): ImportOptions[] {
    return fileOptions.map((option) => ({
      table_name: option.table_name || null,
      header_row: option.header_row,
      text_columns: option.text_columns
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
      sheets: option.sheets
        ? option.sheets
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean)
        : null,
    }));
  }

  function selectFiles(selected: File[]) {
    const limited = datasetId && !replaceVersion ? selected.slice(0, 1) : selected;
    setFiles(limited);
    setFileOptions(limited.map(() => blankOptions()));
  }

  function updateOption(index: number, values: Partial<EditableOptions>) {
    setFileOptions((current) =>
      current.map((option, position) => (position === index ? { ...option, ...values } : option)),
    );
  }

  async function execute(action: () => Promise<Dataset>) {
    setBusy(true);
    try {
      const result = await action();
      toast.success(datasetId ? "Source added" : "Dataset added");
      onComplete?.(result);
      resetAndClose();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not add data");
    } finally {
      setBusy(false);
    }
  }

  async function previewFiles() {
    setBusy(true);
    try {
      const options = optionsPayload();
      if (datasetId && replaceVersion) {
        const version = await datasets.replaceVersion(datasetId, files, options);
        setReviewDatasetId(datasetId);
        setReviewVersion(version);
      } else if (datasetId) {
        await execute(() => datasets.addSource(datasetId, files[0]));
      } else if (reviewDatasetId) {
        const version = await datasets.replaceVersion(reviewDatasetId, files, options);
        setReviewVersion(version);
      } else {
        const staged = await datasets.upload(files, name || undefined, options, true);
        const versions = await datasets.versions(staged.id);
        const version = versions.find((item) => item.id === staged.review_version_id);
        if (!version) throw new Error("Draft version was not returned by the server");
        setReviewDatasetId(staged.id);
        setReviewVersion(version);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not preview import");
    } finally {
      setBusy(false);
    }
  }

  function changeOpen(value: boolean) {
    if (value) {
      setOpen(true);
    } else if (!busy) {
      resetAndClose();
    }
  }

  const addUrl = () =>
    execute(() =>
      datasetId ? datasets.addSource(datasetId, url) : datasets.fromUrl(url, name || undefined),
    );
  const trigger = replaceVersion ? "Upload new version" : datasetId ? "Add source" : "Add data";

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger render={<Button type="button" />}>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-auto">
        {reviewVersion && reviewDatasetId ? (
          <>
            <DialogTitle className="sr-only">Dataset import</DialogTitle>
            <ImportReview
              key={reviewVersion.id}
              datasetId={reviewDatasetId}
              version={reviewVersion}
              onBack={() => setReviewVersion(null)}
              onConfirmed={(dataset) => {
                toast.success("Import confirmed");
                onComplete?.(dataset);
                resetAndClose();
              }}
            />
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>{replaceVersion ? "Upload new version" : trigger}</DialogTitle>
              <DialogDescription>
                {replaceVersion
                  ? "Include every file needed by the report; this replaces the current source set after confirmation. Earlier versions remain available."
                  : "Load files, public links, Google Sheets, or a database."}
              </DialogDescription>
            </DialogHeader>
            <Tabs defaultValue="upload">
              {!replaceVersion && (
                <TabsList className="grid grid-cols-4">
                  <TabsTrigger value="upload">Upload files</TabsTrigger>
                  <TabsTrigger value="url">From URL</TabsTrigger>
                  <TabsTrigger value="sheet">Google Sheets</TabsTrigger>
                  <TabsTrigger value="db" disabled={Boolean(datasetId)}>
                    Database
                  </TabsTrigger>
                </TabsList>
              )}
              <TabsContent value="upload" className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="source-files">Files</Label>
                  <Input
                    id="source-files"
                    type="file"
                    multiple={!datasetId || replaceVersion}
                    accept=".csv,.tsv,.parquet,.json,.jsonl,.xlsx,.xls"
                    onChange={(event) => selectFiles(Array.from(event.target.files ?? []))}
                  />
                </div>
                {!datasetId && (
                  <div className="space-y-2">
                    <Label htmlFor="dataset-name">Dataset name (optional)</Label>
                    <Input
                      id="dataset-name"
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </div>
                )}
                {(!datasetId || replaceVersion) && (
                  <>
                    {files.map((file, index) => {
                      const unsupported = /\.(json|jsonl|parquet)$/i.test(file.name);
                      return (
                        <div key={`${file.name}-${index}`} className="space-y-3 rounded border p-3">
                          <p className="text-sm font-medium">{file.name}</p>
                          <Label htmlFor={`table-name-${index}`}>Table name override</Label>
                          <Input
                            id={`table-name-${index}`}
                            value={fileOptions[index]?.table_name ?? ""}
                            onChange={(event) =>
                              updateOption(index, { table_name: event.target.value })
                            }
                          />
                          <Label htmlFor={`header-row-${index}`}>Header row</Label>
                          <Input
                            id={`header-row-${index}`}
                            type="number"
                            min={1}
                            max={100}
                            disabled={unsupported}
                            value={fileOptions[index]?.header_row ?? 1}
                            onChange={(event) =>
                              updateOption(index, { header_row: Number(event.target.value) })
                            }
                          />
                          <Label htmlFor={`text-columns-${index}`}>Text columns</Label>
                          <Input
                            id={`text-columns-${index}`}
                            disabled={unsupported}
                            placeholder="order_id, sold_on"
                            value={fileOptions[index]?.text_columns ?? ""}
                            onChange={(event) =>
                              updateOption(index, { text_columns: event.target.value })
                            }
                          />
                          {/\.xls/i.test(file.name) && (
                            <>
                              <Label htmlFor={`sheets-${index}`}>Sheet names</Label>
                              <Input
                                id={`sheets-${index}`}
                                placeholder="Orders, Customers"
                                value={fileOptions[index]?.sheets ?? ""}
                                onChange={(event) =>
                                  updateOption(index, { sheets: event.target.value })
                                }
                              />
                            </>
                          )}
                        </div>
                      );
                    })}
                    <p className="text-xs text-muted-foreground">
                      Preserve identifiers and decimal/date text when automatic type inference would
                      change their meaning.
                    </p>
                  </>
                )}
                <Button type="button" disabled={!files.length || busy} onClick={previewFiles}>
                  {busy
                    ? "Preparing..."
                    : datasetId && !replaceVersion
                      ? "Add source"
                      : "Preview import"}
                </Button>
              </TabsContent>
              {!replaceVersion && (
                <>
                  <TabsContent value="url" className="space-y-4">
                    <Label htmlFor="data-url">Data URL</Label>
                    <Input
                      id="data-url"
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
                    <Button type="button" disabled={!url || busy} onClick={addUrl}>
                      Load URL
                    </Button>
                  </TabsContent>
                  <TabsContent value="sheet" className="space-y-4">
                    <Label htmlFor="sheet-url">Google Sheets share link</Label>
                    <Input
                      id="sheet-url"
                      value={url}
                      onChange={(event) => setUrl(event.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">
                      Public links only. Do not make a confidential sheet public; private Sheets
                      authorization is not available yet.
                    </p>
                    {!datasetId && (
                      <Input
                        placeholder="Dataset name (optional)"
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                      />
                    )}
                    <Button type="button" disabled={!url || busy} onClick={addUrl}>
                      Load sheet
                    </Button>
                  </TabsContent>
                  <TabsContent value="db" className="space-y-4">
                    <Label>Existing connection</Label>
                    <Select
                      value={connectionId}
                      onValueChange={(value) => setConnectionId(value ?? "")}
                    >
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
                      type="button"
                      disabled={(!connectionId && !uri) || busy}
                      onClick={() =>
                        execute(() =>
                          datasets.fromConnection({
                            connection_id: connectionId || undefined,
                            uri: uri || undefined,
                            name: name || connectionName || "Connected data",
                            tables: tables
                              ? tables.split(",").map((value) => value.trim())
                              : undefined,
                          }),
                        )
                      }
                    >
                      Connect
                    </Button>
                  </TabsContent>
                </>
              )}
            </Tabs>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
