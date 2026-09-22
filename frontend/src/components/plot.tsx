"use client";

import dynamic from "next/dynamic";
import type { PlotParams } from "react-plotly.js";

const Plotly = dynamic(() => import("react-plotly.js"), { ssr: false });

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
