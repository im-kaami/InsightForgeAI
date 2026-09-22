"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { sessions } from "@/lib/api";

export default function HistoryPage() {
  const query = useQuery({ queryKey: ["sessions"], queryFn: sessions.list });
  const details = useQueries({
    queries: (query.data ?? []).map((session) => ({
      queryKey: ["session-history", session.id],
      queryFn: () => sessions.get(session.id),
    })),
  });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggle(sessionId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) {
        next.delete(sessionId);
      } else {
        next.add(sessionId);
      }
      return next;
    });
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-semibold">History</h1>
        <p className="text-muted-foreground">Return to previous analysis sessions.</p>
      </header>
      <div className="grid gap-4">
        {query.data?.map((session, index) => {
          const runs = details[index]?.data?.runs ?? [];
          const isExpanded = expanded.has(session.id);
          const lastActivity = runs.at(-1)?.finished_at ?? session.created_at;
          return (
            <Card key={session.id}>
              <CardHeader className="flex-row items-center justify-between">
                <div>
                  <CardTitle className="text-lg">
                    <Link className="hover:underline" href={`/sessions/${session.id}`}>
                      {session.title}
                    </Link>
                  </CardTitle>
                  <p className="text-sm text-muted-foreground">
                    {runs.length} runs · {new Date(lastActivity).toLocaleString()}
                  </p>
                </div>
                <Button size="sm" variant="ghost" onClick={() => toggle(session.id)}>
                  {isExpanded ? (
                    <ChevronDown className="size-4" />
                  ) : (
                    <ChevronRight className="size-4" />
                  )}
                  {isExpanded ? "Collapse" : "Expand"}
                </Button>
              </CardHeader>
              {isExpanded && (
                <CardContent className="space-y-2">
                  {runs.length ? (
                    runs.map((run) => (
                      <Link
                        className="flex items-center justify-between rounded-md border p-3 hover:bg-muted"
                        href={`/sessions/${session.id}`}
                        key={run.id}
                      >
                        <div>
                          <p className="text-sm font-medium">{run.goal}</p>
                          <p className="text-xs text-muted-foreground">
                            {new Date(run.finished_at ?? run.created_at).toLocaleString()}
                          </p>
                        </div>
                        <Badge variant="outline">{run.status}</Badge>
                      </Link>
                    ))
                  ) : (
                    <p className="text-sm text-muted-foreground">No runs in this session.</p>
                  )}
                </CardContent>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
