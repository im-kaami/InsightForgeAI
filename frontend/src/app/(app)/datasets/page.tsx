"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { AddDataDialog } from "@/components/add-data-dialog";
import { Badge } from "@/components/ui/badge";
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
import { datasets, sessions } from "@/lib/api";

export default function DatasetsPage() {
  const query = useQuery({ queryKey: ["datasets"], queryFn: datasets.list });
  const client = useQueryClient();
  const router = useRouter();
  async function ask(id: string, name: string) {
    try {
      const session = await sessions.create(id, name);
      router.push(`/sessions/${session.id}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not start session");
    }
  }
  async function remove(id: string) {
    if (!confirm("Delete this dataset?")) return;
    await datasets.remove(id);
    await client.invalidateQueries({ queryKey: ["datasets"] });
    toast.success("Dataset deleted");
  }
  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Datasets</h1>
          <p className="text-muted-foreground">Bring your data together and start an analysis.</p>
        </div>
        <AddDataDialog onComplete={() => client.invalidateQueries({ queryKey: ["datasets"] })} />
      </header>
      <Card>
        <CardHeader>
          <CardTitle>Your data</CardTitle>
        </CardHeader>
        <CardContent>
          {query.data?.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Tables</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.data.map((dataset) => (
                  <TableRow key={dataset.id}>
                    <TableCell>
                      <button
                        className="font-medium hover:underline"
                        onClick={() => router.push(`/datasets/${dataset.id}`)}
                      >
                        {dataset.name}
                      </button>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{dataset.kind}</Badge>
                    </TableCell>
                    <TableCell>{dataset.tables.length}</TableCell>
                    <TableCell>{new Date(dataset.created_at).toLocaleDateString()}</TableCell>
                    <TableCell className="flex justify-end gap-2">
                      <Button size="sm" onClick={() => ask(dataset.id, dataset.name)}>
                        <MessageSquare className="size-4" />
                        Ask
                      </Button>
                      <Button size="icon-sm" variant="ghost" onClick={() => remove(dataset.id)}>
                        <Trash2 className="size-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="py-16 text-center text-muted-foreground">
              No datasets yet. Add files or connect a source to begin.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
