import type { HypothesisStatus, VerdictType } from "../lib/types";

const STATUS_STYLES: Record<HypothesisStatus, string> = {
  proposed: "bg-slate-700/60 text-slate-300",
  testing: "bg-amber-500/15 text-amber-300",
  killed: "bg-red-500/15 text-red-300",
  survived: "bg-emerald-500/15 text-emerald-300",
  promoted: "bg-violet-500/15 text-violet-300",
};

const STATUS_DOT: Record<HypothesisStatus, string> = {
  proposed: "bg-slate-400",
  testing: "bg-amber-400",
  killed: "bg-red-400",
  survived: "bg-emerald-400",
  promoted: "bg-violet-400",
};

export function StatusBadge({ status }: { status: HypothesisStatus }) {
  return (
    <span className={`pill ${STATUS_STYLES[status]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[status]}`} />
      {status}
    </span>
  );
}

const VERDICT_STYLES: Record<VerdictType, string> = {
  survive: "bg-emerald-500/15 text-emerald-300",
  kill: "bg-red-500/15 text-red-300",
  promote: "bg-violet-500/15 text-violet-300",
};

export function VerdictBadge({ type }: { type: VerdictType }) {
  return <span className={`pill ${VERDICT_STYLES[type]} uppercase tracking-wide`}>{type}</span>;
}
