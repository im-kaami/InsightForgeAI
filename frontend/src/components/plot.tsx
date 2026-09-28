"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import type { PlotParams } from "react-plotly.js";
import { Skeleton } from "@/components/ui/skeleton";

const Plotly = dynamic(
  async () => {
    const [{ default: createPlotlyComponent }, { default: PlotlyLibrary }] = await Promise.all([
      import("react-plotly.js/factory"),
      import("plotly.js-dist-min"),
    ]);
    return createPlotlyComponent(PlotlyLibrary);
  },
  {
    ssr: false,
    loading: () => <Skeleton className="h-[420px] w-full" />,
  },
);

const CHART_TYPES = { bar: "Bars", line: "Line", area: "Area", scatter: "Points" } as const;
type ChartType = keyof typeof CHART_TYPES;
type Trace = Record<string, unknown>;

const TRACE_SPECIFIC = [
  "mode",
  "fill",
  "fillcolor",
  "stackgroup",
  "offsetgroup",
  "alignmentgroup",
  "textposition",
  "width",
  "line",
  "marker",
];

function isChartType(value: string | undefined): value is ChartType {
  return value !== undefined && value in CHART_TYPES;
}

function convertTrace(trace: Trace, type: ChartType): Trace {
  if (!["bar", "scatter", "scattergl"].includes(String(trace.type ?? "scatter"))) return trace;
  const marker = trace.marker as { color?: unknown } | undefined;
  const line = trace.line as { color?: unknown } | undefined;
  const color = marker?.color ?? line?.color;
  const base = Object.fromEntries(
    Object.entries(trace).filter(([key]) => !TRACE_SPECIFIC.includes(key)),
  );
  if (type === "bar") return { ...base, type: "bar", marker: { color } };
  if (type === "scatter") return { ...base, type: "scatter", mode: "markers", marker: { color } };
  return {
    ...base,
    type: "scatter",
    mode: "lines",
    line: { color },
    ...(type === "area" ? { stackgroup: "one" } : {}),
  };
}

export function Plot({
  figure,
  kind,
}: {
  figure: { data?: PlotParams["data"]; layout?: PlotParams["layout"] };
  kind?: string;
}) {
  const initial = isChartType(kind) ? kind : undefined;
  const [type, setType] = useState<ChartType | undefined>(initial);
  const data = useMemo(() => {
    const traces = figure.data ?? [];
    if (!type || type === initial) return traces;
    return traces.map((trace) =>
      convertTrace(trace as Trace, type),
    ) as unknown as PlotParams["data"];
  }, [figure.data, type, initial]);
  return (
    <div className="space-y-2">
      {initial && (
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          Show as
          <select
            aria-label="Chart type"
            className="h-7 rounded-md border bg-background px-2 text-xs"
            value={type}
            onChange={(event) => setType(event.target.value as ChartType)}
          >
            {Object.entries(CHART_TYPES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      )}
      <Plotly
        data={data}
        layout={figure.layout ?? { autosize: true }}
        config={{ responsive: true, displaylogo: false }}
        useResizeHandler
        className="h-[420px] w-full"
      />
    </div>
  );
}
