"use client";

import dynamic from "next/dynamic";
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

export function Plot({
  figure,
}: {
  figure: { data?: PlotParams["data"]; layout?: PlotParams["layout"] };
}) {
  return (
    <Plotly
      data={figure.data ?? []}
      layout={figure.layout ?? { autosize: true }}
      config={{ responsive: true, displaylogo: false }}
      useResizeHandler
      className="h-[420px] w-full"
    />
  );
}
