import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Maximize2, X } from "lucide-react";
import type { GraphData, GraphNode } from "../api";
import { Empty, ErrorLine, Panel } from "./ui";

const LABEL_COLOR: Record<string, string> = {
  Tenant: "var(--accent)", Person: "var(--ok)", Habit: "#a3e635", Camera: "#60a5fa", Event: "var(--warn)",
  Object: "#fb923c", Alert: "var(--bad)", NewsContext: "var(--accent2)", SOPDoc: "#f472b6",
};
const FALLBACK = "var(--muted)";
const colorOf = (n: GraphNode): string => {
  for (const l of n.labels) if (LABEL_COLOR[l]) return LABEL_COLOR[l];
  return FALLBACK;
};
interface Canvas { W: number; H: number; step: number; max: number; node: number; center: number; font: number; labelsAll: boolean }
const SMALL: Canvas = { W: 640, H: 440, step: 70, max: 200, node: 9, center: 16, font: 9, labelsAll: false };
const LARGE: Canvas = { W: 1200, H: 800, step: 120, max: 360, node: 12, center: 22, font: 12, labelsAll: true };

/** One query that shows the whole neighbourhood of the tenant, pre-filled in the Neo4j Browser (press Ctrl+Enter there). */
export const neo4jUrl = (tenantId: string): string =>
  `http://localhost:7474/browser/?cmd=edit&arg=${encodeURIComponent(`MATCH p=(t:Tenant {id:'${tenantId}'})-[*1..2]-(n) RETURN p LIMIT 300`)}`;

function nodeTitle(n: GraphNode): string {
  const props = Object.entries(n.props ?? {}).filter(([k]) => !/embedding/i.test(k))
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join("\n");
  return `${n.labels.join(":") || "Node"} ${n.id}${props ? "\n" + props : ""}`;
}
const caption = (n: GraphNode): string => {
  const p = n.props ?? {};
  const v = p.name ?? p.label ?? p.kind ?? p.rule ?? p.id ?? n.id;
  const s = String(v);
  return s.length > 14 ? s.slice(0, 13) + "…" : s;
};

function GraphView({ graph, cv }: { graph: GraphData; cv: Canvas }) {
  const { W, H } = cv, CX = W / 2, CY = H / 2;
  const layout = useMemo(() => {
    if (graph.nodes.length === 0) return null;
    const center = graph.nodes.find((n) => n.labels.includes("Tenant")) ?? graph.nodes[0];
    const others = graph.nodes.filter((n) => n.id !== center.id);
    // group by label so each ring is coherent; ring capacity grows with radius
    const sorted = [...others].sort((a, b) => (a.labels[0] ?? "").localeCompare(b.labels[0] ?? ""));
    const pos = new Map<string, { x: number; y: number }>([[center.id, { x: CX, y: CY }]]);
    let idx = 0, ring = 1;
    while (idx < sorted.length) {
      const r = Math.min(cv.step * ring, cv.max);
      const cap = Math.max(6, Math.floor((2 * Math.PI * r) / (cv.node * 5)));
      const batch = sorted.slice(idx, idx + cap);
      batch.forEach((n, i) => {
        const a = (2 * Math.PI * i) / batch.length + ring * 0.4 - Math.PI / 2;
        pos.set(n.id, { x: CX + r * Math.cos(a), y: CY + r * Math.sin(a) * 0.9 });
      });
      idx += cap; ring += 1;
    }
    return { center, pos, rest: sorted };
  }, [graph, cv, CX, CY]);
  if (!layout) return <Empty>Graph is empty for this tenant.</Empty>;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto block" role="img" aria-label="Knowledge graph">
      {[1, 2, 3].map((k) => { const r = Math.min(cv.step * k, cv.max); return <ellipse key={k} cx={CX} cy={CY} rx={r} ry={r * 0.9} fill="none" stroke="var(--line)" strokeDasharray="2 5" opacity={0.5} />; })}
      {graph.edges.map((e, i) => {
        const a = layout.pos.get(e.source), b = layout.pos.get(e.target);
        if (!a || !b) return null;
        return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--line)" strokeWidth={1}><title>{e.type}</title></line>;
      })}
      {[layout.center, ...layout.rest].map((n) => {
        const p = layout.pos.get(n.id);
        if (!p) return null;
        const isC = n.id === layout.center.id;
        return (
          <g key={n.id}>
            <circle cx={p.x} cy={p.y} r={isC ? cv.center : cv.node} fill={colorOf(n)} fillOpacity={0.85} stroke="var(--field)" strokeWidth={2}>
              <title>{nodeTitle(n)}</title>
            </circle>
            {(isC || cv.labelsAll || layout.rest.length <= 30) && (
              <text x={p.x} y={p.y + (isC ? cv.center + 12 : cv.node + 12)} textAnchor="middle" fontSize={cv.font} fill="var(--muted)" style={{ pointerEvents: "none" }}>{caption(n)}</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function KnowledgeGraph({ graph, loading, error, tenantId }: { graph: GraphData | undefined; loading: boolean; error: string | null; tenantId: string }) {
  const [big, setBig] = useState(false);
  useEffect(() => {
    if (!big) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setBig(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [big]);

  const labelsPresent = useMemo(() => {
    const s = new Set<string>();
    graph?.nodes.forEach((n) => n.labels.forEach((l) => s.add(l)));
    return s;
  }, [graph]);

  const legend = (
    <div className="flex flex-wrap gap-x-3 gap-y-1 mt-3">
      {Object.entries(LABEL_COLOR).map(([l, c]) => (
        <span key={l} className="mono text-[10px] flex items-center gap-1.5" style={{ color: labelsPresent.has(l) ? "var(--ink)" : "var(--muted)", opacity: labelsPresent.has(l) ? 1 : 0.5 }}>
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: c }} />{l}
        </span>
      ))}
    </div>
  );
  const link = (
    <a href={neo4jUrl(tenantId)} target="_blank" rel="noreferrer" title="Opens Neo4j Browser with a query for this site already typed in. Log in, then press Ctrl+Enter."
      className="mono text-[10px] flex items-center gap-1 whitespace-nowrap" style={{ color: "var(--accent)" }}>NEO4J BROWSER <ExternalLink size={11} /></a>
  );

  return (
    <Panel kicker="KNOWLEDGE GRAPH" title="Neo4j subgraph"
      right={<div className="flex items-center gap-3">
        <button onClick={() => setBig(true)} disabled={!graph || graph.nodes.length === 0} aria-label="Expand the graph"
          className="mono text-[10px] flex items-center gap-1 disabled:opacity-40" style={{ color: "var(--accent)" }}>EXPAND <Maximize2 size={11} /></button>
        {link}
      </div>}>
      <ErrorLine msg={error} />
      {loading && !graph && <Empty>Loading graph...</Empty>}
      {graph && (
        <>
          <p className="mono text-[10px] mb-2" style={{ color: "var(--muted)" }}>{graph.nodes.length} nodes · {graph.edges.length} edges</p>
          <div className="rounded-lg border overflow-hidden" style={{ borderColor: "var(--line)", background: "var(--field)" }}>
            <GraphView graph={graph} cv={SMALL} />
          </div>
          {legend}
        </>
      )}
      {big && graph && (
        <div className="fixed inset-0 z-50 p-3 sm:p-6 flex" style={{ background: "rgba(10,16,28,.55)", backdropFilter: "blur(4px)" }} onClick={() => setBig(false)}>
          <div className="m-auto w-full max-w-[1400px] max-h-full overflow-auto rounded-2xl border p-4" style={{ background: "var(--surface)", borderColor: "var(--line)" }} onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Knowledge graph, expanded">
            <div className="flex items-center justify-between gap-3 mb-3">
              <div><p className="mono text-[11px] tracking-[0.2em]" style={{ color: "var(--accent)" }}>KNOWLEDGE GRAPH</p>
                <p className="font-bold">{tenantId} · {graph.nodes.length} nodes · {graph.edges.length} edges</p></div>
              <div className="flex items-center gap-3">{link}
                <button onClick={() => setBig(false)} aria-label="Close" className="p-2 rounded-lg border" style={{ borderColor: "var(--line)", color: "var(--muted)" }}><X size={14} /></button></div>
            </div>
            <div className="rounded-lg border overflow-hidden" style={{ borderColor: "var(--line)", background: "var(--field)" }}><GraphView graph={graph} cv={LARGE} /></div>
            {legend}
            <p className="text-[11px] mt-2" style={{ color: "var(--muted)" }}>Hover a dot for its details. Esc or click outside to close.</p>
          </div>
        </div>
      )}
    </Panel>
  );
}
