import type { SubsystemStatus, VerdictType } from "./types";

/** Semantic verdict colors for SVG marks (matches VerdictBadge / StatusBadge
 * tailwind classes: red-400 / emerald-400 / violet-400, slate-500 = untested).
 * These carry meaning — never reuse the accent blue for verdict encoding. */
export const VERDICT_HEX: Record<VerdictType | "none", string> = {
  kill: "#f87171",
  survive: "#34d399",
  promote: "#a78bfa",
  none: "#64748b",
};

export interface StatusStyle {
  label: string;
  dot: string;
  text: string;
  border: string;
  chip: string;
}

export const SUB_STATUS: Record<SubsystemStatus, StatusStyle> = {
  built: {
    label: "Built",
    dot: "bg-emerald-400",
    text: "text-emerald-300",
    border: "border-l-emerald-400",
    chip: "bg-emerald-500/15 text-emerald-300",
  },
  partial: {
    label: "Scaffolded",
    dot: "bg-amber-400",
    text: "text-amber-300",
    border: "border-l-amber-400",
    chip: "bg-amber-500/15 text-amber-300",
  },
  next: {
    label: "Up next",
    dot: "bg-accent",
    text: "text-accent",
    border: "border-l-accent",
    chip: "bg-accent/15 text-accent",
  },
  planned: {
    label: "Planned",
    dot: "bg-slate-500",
    text: "text-slate-400",
    border: "border-l-slate-600",
    chip: "bg-slate-700/60 text-slate-400",
  },
};
