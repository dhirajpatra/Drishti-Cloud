import type { CSSProperties, ReactNode } from "react";

export function Panel({ kicker, title, right, children, className = "", style }: {
  kicker: string; title?: string; right?: ReactNode; children: ReactNode; className?: string; style?: CSSProperties;
}) {
  return (
    <section className={`rounded-xl border p-4 min-w-0 ${className}`} style={{ background: "var(--surface)", borderColor: "var(--line)", ...style }}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <p className="mono text-[11px] tracking-[0.2em]" style={{ color: "var(--accent)" }}>{kicker}</p>
          {title && <h3 className="font-bold mt-0.5">{title}</h3>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Pill({ color, children, solid = false }: { color: string; children: ReactNode; solid?: boolean }) {
  return (
    <span className="mono text-[10px] px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ borderColor: color, color: solid ? "var(--on-accent)" : color, background: solid ? color : "transparent" }}>
      {children}
    </span>
  );
}

export function Chip({ children, color = "var(--muted)" }: { children: ReactNode; color?: string }) {
  return (
    <span className="mono text-[10px] px-2 py-0.5 rounded border" style={{ borderColor: "var(--line)", color, background: "var(--field)" }}>
      {children}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm py-3" style={{ color: "var(--muted)" }}>{children}</p>;
}

export function ErrorLine({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return <p className="mono text-[11px] mb-2" style={{ color: "var(--bad)" }}>! {msg}</p>;
}

export function Stat({ label, value, sub, color = "var(--accent)" }: { label: string; value: ReactNode; sub?: string; color?: string }) {
  return (
    <div className="rounded-xl p-3 border min-w-0" style={{ background: "var(--surface)", borderColor: "var(--line)" }}>
      <p className="text-2xl font-bold leading-none" style={{ color }}>{value}</p>
      <p className="mono text-[10px] tracking-widest mt-2 uppercase" style={{ color: "var(--muted)" }}>{label}</p>
      {sub && <p className="mono text-[10px] mt-0.5 truncate" style={{ color: "var(--muted)" }}>{sub}</p>}
    </div>
  );
}

export const inputCls = "w-full rounded-lg px-3 py-2 text-sm border outline-none focus:border-[var(--accent)]";
export const inputStyle: CSSProperties = { background: "var(--field)", borderColor: "var(--line)", color: "var(--ink)" };
export const btnPrimary: CSSProperties = { background: "var(--accent)", color: "var(--on-accent)", fontWeight: 700 };
export const btnGhost: CSSProperties = { borderColor: "var(--line)", color: "var(--muted)" };
