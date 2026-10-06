"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BellRing,
  CalendarClock,
  Database,
  Gauge,
  History,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Share2,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { RequireAuth } from "@/components/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { follows, health, models } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

const nav = [
  { href: "/datasets", label: "Datasets", icon: Database },
  { href: "/dashboards", label: "Dashboards", icon: LayoutDashboard },
  { href: "/history", label: "History", icon: History },
  { href: "/schedules", label: "Schedules", icon: CalendarClock },
  { href: "/workspaces", label: "Workspaces", icon: Users },
  { href: "/sharing", label: "Sharing", icon: Share2 },
  { href: "/usage", label: "Usage", icon: Gauge },
  { href: "/api-tokens", label: "API tokens", icon: KeyRound },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: health });
  const alertsQuery = useQuery({
    queryKey: ["model-alerts"],
    queryFn: models.alerts,
    enabled: Boolean(user),
    refetchInterval: 60_000,
  });
  const metricAlertsQuery = useQuery({
    queryKey: ["metric-alerts"],
    queryFn: follows.alerts,
    enabled: Boolean(user),
    refetchInterval: 60_000,
  });
  return (
    <RequireAuth>
      <div className="min-h-screen md:grid md:grid-cols-[240px_1fr]">
        <aside className="flex min-h-screen flex-col border-r bg-background p-4">
          <Link href="/datasets" className="block px-3 py-4 text-xl font-semibold tracking-tight">
            InsightForge
          </Link>
          <Separator className="mb-4" />
          <nav className="space-y-1">
            {nav.map(({ href, label, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm",
                  pathname.startsWith(href)
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon className="size-4" />
                {label}
              </Link>
            ))}
          </nav>
          {alertsQuery.data && alertsQuery.data.length > 0 && (
            <div
              role="status"
              data-testid="model-alerts-nav"
              className="mt-4 space-y-1 rounded-md border border-destructive/40 p-3 text-xs"
            >
              <p className="flex items-center gap-2 font-medium">
                <BellRing className="size-4" />
                Model alerts
              </p>
              {Array.from(
                new Map(alertsQuery.data.map((item) => [item.model_id, item])).values(),
              ).map((item) => (
                <Link
                  key={item.model_id}
                  href={`/datasets/${item.dataset_id}`}
                  className="block truncate hover:underline"
                >
                  {item.model_name ?? "A saved model"}:{" "}
                  {item.status === "failed" ? "check failed" : "retraining recommended"}
                </Link>
              ))}
            </div>
          )}
          {metricAlertsQuery.data && metricAlertsQuery.data.length > 0 && (
            <div
              role="status"
              data-testid="metric-alerts-nav"
              className="mt-4 space-y-1 rounded-md border border-destructive/40 p-3 text-xs"
            >
              <p className="flex items-center gap-2 font-medium">
                <BellRing className="size-4" />
                Metric alerts
              </p>
              {Array.from(
                new Map(metricAlertsQuery.data.map((item) => [item.follow_id, item])).values(),
              ).map((item) => (
                <Link
                  key={item.follow_id}
                  href={
                    item.session_id
                      ? `/sessions/${item.session_id}`
                      : `/datasets/${item.dataset_id}`
                  }
                  className="block truncate hover:underline"
                  title={item.message}
                >
                  {item.metric_label ?? item.metric ?? "A metric"}:{" "}
                  {item.status === "failed"
                    ? "check failed"
                    : `${(item.change_percent ?? 0) > 0 ? "up" : "down"} ${Math.abs(item.change_percent ?? 0)}%`}
                </Link>
              ))}
            </div>
          )}
          <div className="mt-auto space-y-3 pt-8">
            {healthQuery.data?.llm === "fake" ? (
              <Badge variant="outline">Configured model: offline</Badge>
            ) : healthQuery.data ? (
              <p className="px-1 text-xs text-muted-foreground">
                Configured model: {healthQuery.data.provider} · {healthQuery.data.model}
              </p>
            ) : null}
            {healthQuery.data?.local_model && (
              <p className="px-1 text-xs text-muted-foreground">
                Local only: {healthQuery.data.local_model} on this computer
              </p>
            )}
            <div className="rounded-lg border p-3 text-xs text-muted-foreground">
              <p className="truncate">{user?.email}</p>
              <Button
                className="mt-2 w-full justify-start"
                variant="ghost"
                size="sm"
                onClick={logout}
              >
                <LogOut className="size-4" />
                Log out
              </Button>
            </div>
          </div>
        </aside>
        <main className="min-w-0 p-5 md:p-8">{children}</main>
      </div>
    </RequireAuth>
  );
}
