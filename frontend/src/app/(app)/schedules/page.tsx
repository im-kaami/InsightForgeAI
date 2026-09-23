"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { datasets, schedules, sessions } from "@/lib/api";

const presets = [
  ["Daily 09:00", "0 9 * * *"],
  ["Weekly Mon 09:00", "0 9 * * 1"],
  ["Monthly 1st 09:00", "0 9 1 * *"],
];
export default function SchedulesPage() {
  const query = useQuery({ queryKey: ["schedules"], queryFn: schedules.list });
  const data = useQuery({ queryKey: ["datasets"], queryFn: datasets.list });
  const sessionQuery = useQuery({ queryKey: ["sessions"], queryFn: sessions.list });
  const client = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [datasetId, setDatasetId] = useState("");
  const [goal, setGoal] = useState("");
  const [cron, setCron] = useState("0 9 * * *");
  const [timezone, setTimezone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
  async function runNow(scheduleId: string, sessionId: string) {
    const run = await schedules.runNow(scheduleId);
    toast.success(`Run ${run.status}`, {
      action: {
        label: "Open session",
        onClick: () => router.push(`/sessions/${sessionId}`),
      },
    });
  }

  async function create() {
    let session = sessionQuery.data?.find((item) => item.dataset_id === datasetId);
    if (!session) session = await sessions.create(datasetId, "Scheduled analysis");
    await schedules.create({
      dataset_id: datasetId,
      session_id: session.id,
      goal,
      cron,
      timezone,
      enabled: true,
    });
    await client.invalidateQueries({ queryKey: ["schedules"] });
    setOpen(false);
    toast.success("Schedule created");
  }
  return (
    <div className="space-y-6">
      <header className="flex justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Schedules</h1>
          <p className="text-muted-foreground">Automate recurring analysis.</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger render={<Button />}>New schedule</DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>New schedule</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <Label>Dataset</Label>
              <Select value={datasetId} onValueChange={(value) => setDatasetId(value ?? "")}>
                <SelectTrigger>
                  <SelectValue placeholder="Choose dataset" />
                </SelectTrigger>
                <SelectContent>
                  {data.data?.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Label>Goal</Label>
              <Input value={goal} onChange={(event) => setGoal(event.target.value)} />
              <Label>Cron expression</Label>
              <Input value={cron} onChange={(event) => setCron(event.target.value)} />
              <Label>Time zone</Label>
              <Input value={timezone} onChange={(event) => setTimezone(event.target.value)} />
              <p className="text-xs text-muted-foreground">Times are in {timezone}</p>
              <div className="flex flex-wrap gap-2">
                {presets.map(([label, value]) => (
                  <Button key={value} variant="outline" size="sm" onClick={() => setCron(value)}>
                    {label}
                  </Button>
                ))}
              </div>
              <Button disabled={!datasetId || !goal} onClick={create}>
                Create schedule
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </header>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Goal</TableHead>
            <TableHead>Cron</TableHead>
            <TableHead>Next run</TableHead>
            <TableHead>Time zone</TableHead>
            <TableHead>Enabled</TableHead>
            <TableHead>Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {query.data?.map((item) => (
            <TableRow key={item.id}>
              <TableCell>{item.goal}</TableCell>
              <TableCell className="font-mono text-xs">{item.cron}</TableCell>
              <TableCell>
                {item.next_run_at ? new Date(item.next_run_at).toLocaleString() : "—"}
              </TableCell>
              <TableCell>{item.timezone}</TableCell>
              <TableCell>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={async () => {
                    await schedules.update(item.id, { enabled: !item.enabled });
                    client.invalidateQueries({ queryKey: ["schedules"] });
                  }}
                >
                  {item.enabled ? "Enabled" : "Disabled"}
                </Button>
              </TableCell>
              <TableCell className="space-x-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => runNow(item.id, item.session_id)}
                >
                  Run now
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={async () => {
                    await schedules.remove(item.id);
                    client.invalidateQueries({ queryKey: ["schedules"] });
                  }}
                >
                  Delete
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
