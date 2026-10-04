"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { dashboards } from "@/lib/api";

const NEW = "__new__";

export function PinToDashboard({ runId, position }: { runId: string; position: number }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const list = useQuery({ queryKey: ["dashboards"], queryFn: dashboards.list, enabled: open });

  async function pin() {
    if (!target || busy) return;
    setBusy(true);
    try {
      let dashboardId = target;
      if (target === NEW) {
        const name = window.prompt("Name of the new dashboard", "My dashboard");
        if (!name?.trim()) return;
        dashboardId = (await dashboards.create(name.trim())).id;
      }
      await dashboards.addItem(dashboardId, { kind: "pinned", run_id: runId, position });
      await client.invalidateQueries({ queryKey: ["dashboards"] });
      await client.invalidateQueries({ queryKey: ["dashboard", dashboardId] });
      toast.success("Pinned to the dashboard");
      setOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not pin the result");
    } finally {
      setBusy(false);
    }
  }

  if (!open)
    return (
      <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
        Pin to dashboard
      </Button>
    );
  return (
    <span className="inline-flex flex-wrap items-center gap-2" data-testid="pin-to-dashboard">
      <select
        aria-label="Dashboard to pin to"
        className="h-8 rounded-md border bg-background px-2 text-sm"
        value={target}
        onChange={(event) => setTarget(event.target.value)}
      >
        <option value="">Choose a dashboard</option>
        {(list.data ?? []).map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}
          </option>
        ))}
        <option value={NEW}>New dashboard...</option>
      </select>
      <Button type="button" size="sm" onClick={pin} disabled={!target || busy}>
        Pin
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
        Cancel
      </Button>
    </span>
  );
}
