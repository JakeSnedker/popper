import type { Subsystem, View } from "../../lib/types";
import { SUB_STATUS } from "../../lib/status";

interface Props {
  subsystems: Subsystem[];
  pipeline: string[];
  cross: string[];
  view: View;
  onNavigate: (v: View) => void;
}

function isActive(view: View, target: View): boolean {
  if (view.kind !== target.kind) return false;
  if (view.kind === "subsystem" && target.kind === "subsystem") return view.id === target.id;
  return true;
}

function NavLink({
  label,
  active,
  onClick,
  dot,
  meta,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  dot?: string;
  meta?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition text-left ${
        active ? "bg-ink-600 text-slate-100" : "text-slate-400 hover:bg-ink-700 hover:text-slate-200"
      }`}
    >
      {dot && <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${dot}`} />}
      <span className="flex-1 truncate">{label}</span>
      {meta && <span className="text-[10px] font-mono text-slate-600">{meta}</span>}
    </button>
  );
}

function Group({ title }: { title: string }) {
  return (
    <div className="px-3 pt-4 pb-1 text-[10px] uppercase tracking-wider text-slate-600 font-semibold">
      {title}
    </div>
  );
}

export function Sidebar({ subsystems, pipeline, cross, view, onNavigate }: Props) {
  const byId = (id: string) => subsystems.find((s) => s.id === id)!;

  return (
    <nav className="w-60 shrink-0 h-screen bg-ink-800 border-r border-ink-600 flex flex-col">
      <div className="px-4 py-4 flex items-center gap-2.5 border-b border-ink-600">
        <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-accent to-violet-500 flex items-center justify-center font-bold text-white text-sm">
          P
        </div>
        <div>
          <div className="text-sm font-semibold text-slate-100 leading-none">Popper</div>
          <div className="text-[10px] text-slate-500 mt-0.5">Research Platform</div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-2 px-2">
        <Group title="Overview" />
        <NavLink
          label="Control Center"
          active={isActive(view, { kind: "home" })}
          onClick={() => onNavigate({ kind: "home" })}
        />
        <NavLink
          label="Roadmap"
          active={isActive(view, { kind: "roadmap" })}
          onClick={() => onNavigate({ kind: "roadmap" })}
        />

        <Group title="Research Loop" />
        {pipeline.map((id) => {
          const s = byId(id);
          return (
            <NavLink
              key={id}
              label={s.name}
              dot={SUB_STATUS[s.status].dot}
              meta={`P${s.phase}`}
              active={isActive(view, { kind: "subsystem", id })}
              onClick={() => onNavigate({ kind: "subsystem", id })}
            />
          );
        })}

        <Group title="Cross-cutting" />
        {cross.map((id) => {
          const s = byId(id);
          return (
            <NavLink
              key={id}
              label={s.name}
              dot={SUB_STATUS[s.status].dot}
              meta={`P${s.phase}`}
              active={isActive(view, { kind: "subsystem", id })}
              onClick={() => onNavigate({ kind: "subsystem", id })}
            />
          );
        })}
      </div>
    </nav>
  );
}
