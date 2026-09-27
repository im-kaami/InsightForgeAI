"use client";

import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Database, History, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { RequireAuth } from "@/components/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { health } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

const nav = [
  { href: "/datasets", label: "Datasets", icon: Database },
  { href: "/history", label: "History", icon: History },
  { href: "/schedules", label: "Schedules", icon: CalendarClock },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: health });
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
          <div className="mt-auto space-y-3 pt-8">
            {healthQuery.data?.llm === "fake" ? (
              <Badge variant="outline">Configured model: offline</Badge>
            ) : healthQuery.data ? (
              <p className="px-1 text-xs text-muted-foreground">
                Configured model: {healthQuery.data.provider} · {healthQuery.data.model}
              </p>
            ) : null}
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
