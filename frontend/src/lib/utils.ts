export { cn } from "cn";

export function formatUsd(value: number | null | undefined) {
  if (value === null || value === undefined) return "\u2014";
  const [whole, fraction] = value.toFixed(4).split(".");
  return `$${whole}.${fraction.replace(/0+$/, "").padEnd(2, "0")}`;
}
