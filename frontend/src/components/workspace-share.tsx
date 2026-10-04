"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { workspaces } from "@/lib/api";

/** Choose the workspace a dataset or dashboard is shared with (owners only). */
export function WorkspaceShare({
  value,
  onChange,
  label,
}: {
  value: string | null | undefined;
  onChange: (workspaceId: string | null) => Promise<unknown>;
  label: string;
}) {
  const client = useQueryClient();
  const list = useQuery({ queryKey: ["workspaces"], queryFn: workspaces.list });
  const [busy, setBusy] = useState(false);
  if (!list.data?.length && !value) return null;
  return (
    <label className="flex items-center gap-2 text-sm" data-testid="workspace-share">
      <span className="text-muted-foreground">{label}</span>
      <select
        aria-label={label}
        className="h-8 rounded-md border bg-background px-2 text-sm"
        value={value ?? ""}
        disabled={busy}
        onChange={async (event) => {
          setBusy(true);
          try {
            await onChange(event.target.value || null);
            await client.invalidateQueries({ queryKey: ["workspaces"] });
            toast.success(event.target.value ? "Shared with the workspace" : "No longer shared");
          } catch (error) {
            toast.error(error instanceof Error ? error.message : "Could not change sharing");
          } finally {
            setBusy(false);
          }
        }}
      >
        <option value="">Only me</option>
        {(list.data ?? []).map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}
          </option>
        ))}
      </select>
    </label>
  );
}
