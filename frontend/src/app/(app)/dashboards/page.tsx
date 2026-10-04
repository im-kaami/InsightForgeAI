"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { dashboards } from "@/lib/api";

export default function DashboardsPage() {
  const client = useQueryClient();
  const router = useRouter();
  const query = useQuery({ queryKey: ["dashboards"], queryFn: dashboards.list });
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function create() {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      const created = await dashboards.create(name.trim());
      await client.invalidateQueries({ queryKey: ["dashboards"] });
      router.push(`/dashboards/${created.id}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not create the dashboard");
      setBusy(false);
    }
  }

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold">Dashboards</h1>
        <p className="text-muted-foreground">
          Collect results in one place: pin tables and charts from any analysis, and add approved
          metric reports and approved questions that refresh on the current data with tested code.
        </p>
      </div>
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void create();
        }}
      >
        <Input
          aria-label="New dashboard name"
          placeholder="Weekly numbers"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <Button type="submit" disabled={busy || !name.trim()}>
          New dashboard
        </Button>
      </form>
      {query.data && query.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No dashboards yet.</p>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2" data-testid="dashboard-list">
        {(query.data ?? []).map((item) => (
          <Link key={item.id} href={`/dashboards/${item.id}`}>
            <Card className="h-full hover:border-primary">
              <CardHeader>
                <CardTitle className="text-base">{item.name}</CardTitle>
              </CardHeader>
              <CardContent className="text-sm text-muted-foreground">
                {item.item_count} tile{item.item_count === 1 ? "" : "s"}
                {item.description ? ` · ${item.description}` : ""}
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
