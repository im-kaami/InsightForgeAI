"use client";

import { useEffect, useState } from "react";
import { DashboardTileBody } from "@/components/dashboard-tile";
import { DataTable } from "@/components/data-table";
import { Markdown } from "@/components/markdown";
import { Plot } from "@/components/plot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { shares, type DashboardItem, type SharedView } from "@/lib/api";

function RunContent({ content }: { content: Record<string, unknown> }) {
  const artifacts = (content.artifacts ?? []) as Record<string, unknown>[];
  const provenance = (content.provenance ?? {}) as Record<string, unknown>;
  const assumptions = (provenance.assumptions ?? []) as string[];
  return (
    <div className="space-y-5">
      <div className="ml-auto max-w-2xl rounded-2xl bg-primary px-4 py-3 text-sm text-primary-foreground">
        {String(content.goal ?? "")}
      </div>
      {artifacts.map((item, index) => {
        if (item.type === "table")
          return (
            <section key={index} className="space-y-2">
              <h3 className="font-medium">{String(item.name)}</h3>
              {item.metric ? (
                <Badge>
                  Approved metric: {String((item.metric as Record<string, unknown>).label ?? "")}
                </Badge>
              ) : item.approved_query ? (
                <Badge>Approved query</Badge>
              ) : null}
              <DataTable
                columns={(item.columns as string[]) ?? []}
                rows={(item.rows as Record<string, unknown>[]) ?? []}
                totalRows={Number(item.total_rows ?? 0)}
                truncated={Boolean(item.truncated)}
              />
            </section>
          );
        if (item.type === "plot")
          return (
            <section key={index}>
              <h3 className="font-medium">{String(item.title || item.name)}</h3>
              <Plot
                figure={(item.figure as { data?: never[]; layout?: object }) ?? {}}
                kind={String(item.kind)}
              />
            </section>
          );
        if (item.type === "text") return <Markdown key={index}>{String(item.text ?? "")}</Markdown>;
        return null;
      })}
      {assumptions.length > 0 ? (
        <details className="rounded border p-3 text-sm">
          <summary className="font-medium">Assumptions</summary>
          <ul className="mt-2 list-disc pl-5">
            {assumptions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

export default function SharedPage() {
  const [view, setView] = useState<SharedView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The secret is after "#", which browsers never send to a server, so it stays out of logs.
    const secret = window.location.hash.slice(1);
    (secret ? shares.view(secret) : Promise.reject(new Error("This link is incomplete.")))
      .then(setView)
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : "This link could not be opened."),
      );
  }, []);

  if (error)
    return (
      <main className="mx-auto max-w-2xl p-8">
        <p role="alert" data-testid="share-error">
          {error}
        </p>
      </main>
    );
  if (!view) return <main className="p-8 text-muted-foreground">Loading...</main>;
  const content = view.content;
  return (
    <main className="mx-auto max-w-6xl space-y-6 p-6 md:p-10" data-testid="shared-view">
      <header>
        <p className="text-sm text-muted-foreground">
          Shared from InsightForge · read only · link expires{" "}
          {new Date(view.expires_at).toLocaleDateString()}
        </p>
        <h1 className="text-3xl font-semibold">{view.title}</h1>
        {content.description ? (
          <p className="text-muted-foreground">{String(content.description)}</p>
        ) : null}
      </header>
      {content.type === "dashboard" ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {((content.items ?? []) as DashboardItem[]).map((item) => (
            <Card key={item.id} data-testid="shared-tile">
              <CardHeader>
                <CardTitle className="text-base">{item.title}</CardTitle>
              </CardHeader>
              <CardContent>
                <DashboardTileBody item={item} />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <RunContent content={content} />
      )}
    </main>
  );
}
