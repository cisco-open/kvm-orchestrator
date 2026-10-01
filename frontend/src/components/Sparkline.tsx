/**
 * Tiny dependency-free SVG line chart for inline use in tables and cards.
 *
 * Renders a single filled-line series with an optional gradient under the
 * line.  Designed for sparkline-sized footprints (~140x40) but scales to
 * whatever width/height you give it.  Empty / single-point series render
 * a neutral "no data" placeholder.
 */

import type { CSSProperties } from "react";

export interface SparklinePoint {
  t: number;   // unix timestamp (seconds)
  v: number;   // value to plot
}

interface SparklineProps {
  data: SparklinePoint[];
  width?: number;
  height?: number;
  /** y-axis floor (default = auto-min) */
  yMin?: number;
  /** y-axis ceiling (default = auto-max with 10% headroom) */
  yMax?: number;
  /** stroke colour, e.g. "#2563eb" (blue-600) */
  color?: string;
  /** fill colour (gradient) under the line, e.g. "#3b82f680" */
  fill?: string;
  /** show last value as a label on the right */
  showLast?: boolean;
  /** unit suffix for the last-value label, e.g. "%" or " MB" */
  unit?: string;
  /** number of decimals on the last-value label */
  decimals?: number;
  /** className applied to the root <div> */
  className?: string;
  /** style applied to the root <div> */
  style?: CSSProperties;
  /** tooltip on hover */
  title?: string;
}

export function Sparkline({
  data,
  width = 140,
  height = 40,
  yMin,
  yMax,
  color = "#2563eb",
  fill,
  showLast = true,
  unit = "",
  decimals = 0,
  className,
  style,
  title,
}: SparklineProps) {
  // Empty state
  if (!data || data.length === 0) {
    return (
      <div
        className={className}
        style={{ width, height, ...style }}
        title={title}
      >
        <div className="flex items-center justify-center w-full h-full text-xs text-gray-400">
          no data
        </div>
      </div>
    );
  }

  // Reserve room for the right-side value label (if shown)
  const labelW = showLast ? 44 : 0;
  const plotW = Math.max(width - labelW, 10);
  const plotH = height;

  const xs = data.map((d) => d.t);
  const ys = data.map((d) => d.v);
  const tMin = Math.min(...xs);
  const tMax = Math.max(...xs);
  const lo = yMin ?? Math.min(...ys);
  const hi = yMax ?? (Math.max(...ys) * 1.1 || 1);
  const range = hi - lo || 1;
  const tRange = tMax - tMin || 1;

  // pad 2px top/bottom so the stroke isn't clipped
  const pad = 2;
  const innerH = plotH - pad * 2;

  const pts: Array<readonly [number, number]> = data.map((d) => {
    const x = ((d.t - tMin) / tRange) * (plotW - 1);
    const y = pad + innerH - ((d.v - lo) / range) * innerH;
    return [x, y] as const;
  });

  const linePath = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const firstPt = pts[0]!;
  const lastPt = pts[pts.length - 1]!;
  const fillPath = `${linePath} L${lastPt[0].toFixed(2)},${plotH} L${firstPt[0].toFixed(2)},${plotH} Z`;

  const last = data[data.length - 1]!.v;
  const gradId = `sparkline-grad-${Math.random().toString(36).slice(2, 9)}`;

  return (
    <div
      className={className}
      style={{ width, height, display: "inline-flex", alignItems: "center", ...style }}
      title={title}
    >
      <svg width={plotW} height={plotH} style={{ overflow: "visible" }}>
        {fill && (
          <>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={fill} stopOpacity="0.5" />
                <stop offset="100%" stopColor={fill} stopOpacity="0" />
              </linearGradient>
            </defs>
            <path d={fillPath} fill={`url(#${gradId})`} stroke="none" />
          </>
        )}
        <path
          d={linePath}
          fill="none"
          stroke={color}
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <circle
          cx={lastPt[0]}
          cy={lastPt[1]}
          r={2}
          fill={color}
        />
      </svg>
      {showLast && (
        <span
          className="text-xs font-mono text-gray-700 ml-1 tabular-nums"
          style={{ minWidth: labelW - 4, textAlign: "right" }}
        >
          {last.toFixed(decimals)}
          {unit}
        </span>
      )}
    </div>
  );
}
