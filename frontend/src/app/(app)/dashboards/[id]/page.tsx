"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { DashboardTileBody } from "@/components/dashboard-tile";
import { ShareButton } from "@/components/share-button";
import { WorkspaceShare } from "@/components/workspace-share";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { dashboards, datasets, workspaces, type DashboardItemIn } from "@/lib/api";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

function AddTile({ dashboardId, onDone }: { dashboardId: string; onDone: () => Promise<void> }) {
  const list = useQuery({ queryKey: ["datasets"], queryFn: datasets.list });
  const [datasetId, setDatasetId] = useState("");
  const [choice, setChoice] = useState("");
  const [days, setDays] = useState("30");
  const [busy, setBusy] = useState(false);
  const detail = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => datasets.get(datasetId),
    enabled: Boolean(datasetId),
  });
  const metrics = (detail.data?.metrics?.metrics ?? []).filter(
    (item) => item.approved && item.date_column,
  );
  const questions = (detail.data?.queries?.queries ?? []).filter((item) => item.approved);

  async function add() {
    if (!choice || busy) return;
    const [kind, value] = choice.split(":", 2);
    const body: DashboardItemIn =
      kind === "metric"
        ? { kind: "metric", dataset_id: datasetId, metric: value, days: Number(days) || 30 }
        : { kind: "question", dataset_id: datasetId, query_id: value };
    setBusy(true);
    try {
      await dashboards.addItem(dashboardId, body);
      setChoice("");
      await onDone();
      toast.success("Tile added");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not add the tile");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card data-testid="add-tile">
      <CardHeader>
        <CardTitle className="text-base">Add a live tile</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-wrap items-end gap-3 text-sm">
        <label className="space-y-1">
          <span className="block text-xs text-muted-foreground">Dataset</span>
          <select
            aria-label="Tile dataset"
            className={selectClass}
            value={datasetId}
            onChange={(event) => {
              setDatasetId(event.target.value);
              setChoice("");
            }}
          >
            <option value="">Choose a dataset</option>
            {(list.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="block text-xs text-muted-foreground">Show</span>
          <select
            aria-label="Tile content"
            className={selectClass}
            value={choice}
            disabled={!datasetId}
            onChange={(event) => setChoice(event.target.value)}
          >
            <option value="">Choose a metric or question</option>
            {metrics.map((item) => (
              <option key={`m-${item.name}`} value={`metric:${item.name}`}>
                Metric report: {item.label || item.name}
              </option>
            ))}
            {questions.map((item) => (
              <option key={`q-${item.id}`} value={`question:${item.id}`}>
                Question: {item.question}
              </option>
            ))}
          </select>
        </label>
        {choice.startsWith("metric:") ? (
          <label className="space-y-1">
            <span className="block text-xs text-muted-foreground">Last N days</span>
            <Input
              aria-label="Tile days"
              className="w-24"
              inputMode="numeric"
              value={days}
              onChange={(event) => setDays(event.target.value)}
            />
          </label>
        ) : null}
        <Button type="button" onClick={add} disabled={!choice || busy}>
          {busy ? "Adding..." : "Add tile"}
        </Button>
        <p className="w-full text-xs text-muted-foreground">
          Only approved metrics (with a date column) and approved questions can be live tiles. To
          add any other result, use &quot;Pin to dashboard&quot; under it in a session.
        </p>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["dashboard", id], queryFn: () => dashboards.get(id) });
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const reload = () => client.invalidateQueries({ queryKey: ["dashboard", id] });

  async function act(action: () => Promise<unknown>, done?: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await action();
      await reload();
      if (done) toast.success(done);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Something went wrong");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const board = query.data;
  if (!board) return <p className="text-muted-foreground">Loading...</p>;
  const items = board.items ?? [];
  const mine = board.access === "own";
  const live = mine && items.some((item) => item.kind !== "pinned");
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/dashboards" className="text-sm text-muted-foreground hover:underline">
            Dashboards
          </Link>
          <h1 className="text-3xl font-semibold">{board.name}</h1>
          {board.description ? <p className="text-muted-foreground">{board.description}</p> : null}
        </div>
        <div className="flex flex-wrap items-start gap-2">
          {board.access !== "own" ? (
            <span className="text-sm text-muted-foreground" data-testid="dashboard-view-only">
              Shared with you: view only
            </span>
          ) : (
            <>
              <WorkspaceShare
                label="Show to workspace"
                value={board.workspace_id}
                onChange={async (workspaceId) => {
                  await workspaces.shareDashboard(id, workspaceId);
                  await reload();
                }}
              />
              <ShareButton kind="dashboard" targetId={id} />
            </>
          )}
          {live ? (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => act(() => dashboards.refresh(id), "Dashboard refreshed")}
            >
              {busy ? "Working..." : "Refresh all"}
            </Button>
          ) : null}
          {mine ? (
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={async () => {
                if (!window.confirm(`Delete the dashboard "${board.name}"? Its tiles are removed.`))
                  return;
                await act(() => dashboards.remove(id));
                await client.invalidateQueries({ queryKey: ["dashboards"] });
                router.push("/dashboards");
              }}
            >
              Delete dashboard
            </Button>
          ) : null}
        </div>
      </header>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No tiles yet. Add a live tile below, or pin a result from a session.
        </p>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2" data-testid="dashboard-tiles">
        {items.map((item, index) => (
          <Card key={item.id} data-testid="dashboard-tile">
            <CardHeader className="flex-row items-start justify-between gap-2">
              <CardTitle className="text-base">{item.title}</CardTitle>
              <div className={mine ? "flex shrink-0 gap-1" : "hidden"}>
                {item.kind !== "pinned" ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => act(() => dashboards.refreshItem(id, item.id), "Tile refreshed")}
                  >
                    Refresh
                  </Button>
                ) : null}
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Move ${item.title} up`}
                  disabled={busy || index === 0}
                  onClick={() => act(() => dashboards.move(id, item.id, -1))}
                >
                  Up
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Move ${item.title} down`}
                  disabled={busy || index === items.length - 1}
                  onClick={() => act(() => dashboards.move(id, item.id, 1))}
                >
                  Down
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove ${item.title}`}
                  disabled={busy}
                  onClick={() => act(() => dashboards.removeItem(id, item.id), "Tile removed")}
                >
                  Remove
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <DashboardTileBody item={item} />
            </CardContent>
          </Card>
        ))}
      </div>
      {mine ? <AddTile dashboardId={id} onDone={async () => void (await reload())} /> : null}
    </div>
  );
}
