import { useMemo } from "react";
import { ExternalLink } from "lucide-react";
import type { GraphData, GraphNode } from "../api";
import { Empty, ErrorLine, Panel } from "./ui";

const LABEL_COLOR: Record<string, string> = {
  Tenant: "#22d3ee", Person: "#34d399", Habit: "#a3e635", Camera: "#60a5fa", Event: "#fbbf24",
  Object: "#fb923c", Alert: "#f87171", NewsContext: "#a78bfa", SOPDoc: "#f472b6",
};
const FALLBACK = "#8ea3c2";
const colorOf = (n: GraphNode): string => {
  for (const l of n.labels) if (LABEL_COLOR[l]) return LABEL_COLOR[l];
  return FALLBACK;
};
const W = 640, H = 440, CX = W / 2, CY = H / 2;

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

export function KnowledgeGraph({ graph, loading, error }: { graph: GraphData | undefined; loading: boolean; error: string | null }) {
  const layout = useMemo(() => {
    if (!graph || graph.nodes.length === 0) return null;
    const center = graph.nodes.find((n) => n.labels.includes("Tenant")) ?? graph.nodes[0];
    const others = graph.nodes.filter((n) => n.id !== center.id);
    // group by label so each ring is coherent; ring capacity grows with radius
    const sorted = [...others].sort((a, b) => (a.labels[0] ?? "").localeCompare(b.labels[0] ?? ""));
    const pos = new Map<string, { x: number; y: number }>([[center.id, { x: CX, y: CY }]]);
    let idx = 0, ring = 1;
    while (idx < sorted.length) {
      const r = Math.min(70 * ring, 200);
      const cap = Math.max(6, Math.floor((2 * Math.PI * r) / 46));
      const batch = sorted.slice(idx, idx + cap);
      batch.forEach((n, i) => {
        const a = (2 * Math.PI * i) / batch.length + ring * 0.4 - Math.PI / 2;
        pos.set(n.id, { x: CX + r * Math.cos(a), y: CY + r * Math.sin(a) * 0.9 });
      });
      idx += cap; ring += 1;
    }
    return { center, pos, rest: sorted };
  }, [graph]);

  const labelsPresent = useMemo(() => {
    const s = new Set<string>();
    graph?.nodes.forEach((n) => n.labels.forEach((l) => s.add(l)));
    return s;
  }, [graph]);

  return (
    <Panel kicker="KNOWLEDGE GRAPH" title="Neo4j subgraph"
      right={<a href="http://localhost:7474" target="_blank" rel="noreferrer" className="mono text-[10px] flex items-center gap-1 whitespace-nowrap" style={{ color: "var(--accent)" }}>NEO4J BROWSER <ExternalLink size={11} /></a>}>
      <ErrorLine msg={error} />
      {loading && !graph && <Empty>Loading graph...</Empty>}
      {graph && !layout && <Empty>Graph is empty for this tenant.</Empty>}
      {graph && layout && (
        <>
          <p className="mono text-[10px] mb-2" style={{ color: "var(--muted)" }}>{graph.nodes.length} nodes · {graph.edges.length} edges</p>
          <div className="rounded-lg border overflow-hidden" style={{ borderColor: "var(--line)", background: "#05080f" }}>
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto block" role="img" aria-label="Knowledge graph">
              {[70, 140, 200].map((r) => <ellipse key={r} cx={CX} cy={CY} rx={r} ry={r * 0.9} fill="none" stroke="#1e2f4a" strokeDasharray="2 5" opacity={0.5} />)}
              {graph.edges.map((e, i) => {
                const a = layout.pos.get(e.source), b = layout.pos.get(e.target);
                if (!a || !b) return null;
                return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#2b4266" strokeWidth={1}><title>{e.type}</title></line>;
              })}
              {[layout.center, ...layout.rest].map((n) => {
                const p = layout.pos.get(n.id);
                if (!p) return null;
                const isC = n.id === layout.center.id;
                return (
                  <g key={n.id}>
                    <circle cx={p.x} cy={p.y} r={isC ? 16 : 9} fill={colorOf(n)} fillOpacity={0.85} stroke="#05080f" strokeWidth={2}>
                      <title>{nodeTitle(n)}</title>
                    </circle>
                    {(isC || layout.rest.length <= 30) && (
                      <text x={p.x} y={p.y + (isC ? 28 : 21)} textAnchor="middle" fontSize={9} fill="#8ea3c2" style={{ fontFamily: "JetBrains Mono, monospace", pointerEvents: "none" }}>{caption(n)}</text>
                    )}
                  </g>
                );
              })}
            </svg>
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 mt-3">
            {Object.entries(LABEL_COLOR).map(([l, c]) => (
              <span key={l} className="mono text-[10px] flex items-center gap-1.5" style={{ color: labelsPresent.has(l) ? "var(--ink)" : "var(--muted)", opacity: labelsPresent.has(l) ? 1 : 0.5 }}>
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: c }} />{l}
              </span>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}
