import { useEffect, useState } from "react";
import { getToken } from "../api";
import { Activity, Bell, Brain, Camera, Cloud, Copy, Check, Cpu, Database, Eye, GitBranch, Layers, Mic, Network, Radio, Server, Shield, Sparkles, Thermometer, Volume2, Waves, Zap, ChevronRight, Home, Newspaper, MessageSquare } from "lucide-react";

const badges = ["Multi-Agent","Multi-Tenant","Multi-Modal","Neo4j","Docker Compose","Meta Model API","SAM 3.1","Muse Spark · Wonder · Image"];

const agents = [
  { id:"perception", name:"Perception Agent", model:"SAM 3.1 · Meta Model API", color:"#22d3ee", icon:Eye, desc:"Segments every frame into objects, masks & tracks. Converts pixels → scene-graph entities.", io:"in: frame.jpg → out: {objects:[{label,mask,bbox,track_id}]}" },
  { id:"memory", name:"Memory Agent", model:"Llama open-weights (local) + Neo4j", color:"#34d399", icon:Database, desc:"5-person face embeddings per home, habit graph, object permanence across occlusions.", io:"in: embedding → out: person_id, confidence, last_seen" },
  { id:"spark", name:"Spark Guard Agent", model:"Muse Spark · Meta API", color:"#fbbf24", icon:Zap, desc:"Fast-path sentinel. Evaluates scene graph every 30s — fire / fall / intruder / child-near-stairs.", io:"in: scene_graph → out: {risk:0-1, alert_type} in <800ms" },
  { id:"wonder", name:"Wonder Reasoning Agent", model:"Muse Wonder · Meta API", color:"#a78bfa", icon:Brain, desc:"Slow-path reasoner. Habit learning, chain-of-thought over Neo4j context + news. Explains WHY.", io:"in: graph_context + news → out: decision + rationale" },
  { id:"image", name:"Image Alert Agent", model:"Muse Image · Meta API", color:"#f472b6", icon:Camera, desc:"Generates annotated visual proof — bbox overlay, caption, before/after diff for the alert card.", io:"in: frame + alert → out: annotated_image_url" },
  { id:"news", name:"News & Context Agent", model:"Wonder + Web Search", color:"#38bdf8", icon:Newspaper, desc:"Grounds decisions in the real world: power cuts, AQI, weather warnings, local incidents.", io:"in: location → out: NewsContext nodes in Neo4j" },
  { id:"action", name:"Action Agent", model:"Policy engine + Wonder veto", color:"#fb923c", icon:MessageSquare, desc:"Decides the actuation: speak on Pi, WhatsApp, ISD1820 hardware alarm, or stay silent.", io:"in: decision → out: MQTT actuation + push" },
];

const codeTabs: Record<string,{lang:string,code:string}> = {
  perception: { lang:"perception_agent.py", code:`# Perception Agent — SAM 3.1 via Meta Responses API
import os, json, base64, httpx
from redis_bus import publish

BASE = "https://api.meta.ai/v1/"
client = httpx.Client(base_url=BASE, headers={
    "Authorization": f"Bearer {os.environ['LLAMA_API_KEY']}"})

def segment(frame_bytes: bytes, tenant_id: str) -> dict:
    b64 = base64.b64encode(frame_bytes).decode()
    r = client.post("responses", json={
        "model": "sam-3.1",
        "input": [{"role": "user", "content": [
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
            {"type": "input_text", "text": "Segment visible people, pets, doors, stairs, stove. Return JSON masks+bboxes."}]}],
        "max_output_tokens": 600})
    r.raise_for_status()
    scene = json.loads(r.json()["output_text"])
    scene["tenant_id"] = tenant_id
    publish("scene.graph", scene)          # → Redis queue
    return scene`},
  spark: { lang:"spark_guard_agent.py", code:`# Spark Guard — Muse Spark fast alert loop (every 30 s)
import os, json, httpx
from redis_bus import subscribe, publish

client = httpx.Client(base_url="https://api.meta.ai/v1/",
    headers={"Authorization": f"Bearer {os.environ['LLAMA_API_KEY']}"})

SYSTEM = """You are Spark Guard, a home-safety sentinel.
Given a scene graph, return ONLY JSON:
{"risk":0..1,"type":"fire|fall|intruder|child_hazard|none","reason":"..."}"""

for msg in subscribe("scene.graph"):
    r = client.post("responses", json={
        "model": "muse-spark-1",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": SYSTEM + "\\n" + json.dumps(msg)}]}],
        "max_output_tokens": 350})
    r.raise_for_status()
    verdict = json.loads(r.json()["output_text"])
    if verdict["risk"] >= 0.7:
        publish("alert.candidate", {**verdict, "tenant_id": msg["tenant_id"]})`},
  wonder: { lang:"wonder_reasoner.py", code:`# Wonder Reasoner — Muse Wonder + Neo4j context + live news
import os, json, httpx
from neo4j import GraphDatabase

client = httpx.Client(base_url="https://api.meta.ai/v1/",
    headers={"Authorization": f"Bearer {os.environ['LLAMA_API_KEY']}"})
graph = GraphDatabase.driver("bolt://neo4j:7687",
    auth=("neo4j", os.environ["NEO4J_PASSWORD"]))

def context(tenant_id: str) -> str:
    q = """MATCH (t:Tenant {id:$tid})-[:KNOWS]->(p:Person)
           OPTIONAL MATCH (p)-[:HABIT_OF]->(h:Habit)
           OPTIONAL MATCH (n:NewsContext)-[:RELATED_TO_NEWS]->(t)
           RETURN collect(p.name) AS people,
                  collect(h.pattern) AS habits,
                  collect(n.headline) AS news"""
    with graph.session() as s:
        return json.dumps(s.run(q, tid=tenant_id).single().data())

def reason(alert: dict) -> dict:
    ctx = context(alert["tenant_id"])
    prompt = f"""Alert: {json.dumps(alert)}
Home context (graph + news): {ctx}
Think step-by-step: is this a true emergency or a known habit?
Consider news context (e.g. power cut explains darkness).
Return JSON: {{act: speak|whatsapp|alarm|ignore, rationale}}"""
    r = client.post("responses", json={
        "model": "muse-wonder-1",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "max_output_tokens": 250})
    r.raise_for_status()
    return json.loads(r.json()["output_text"])`},
  memory: { lang:"memory_agent.py", code:`# Memory Agent — Llama open-weights (local) + Neo4j graph writes
import numpy as np
from neo4j import GraphDatabase
from llama_local import embed_face   # open-weights on Pi-adjacent GPU / Modal

driver = GraphDatabase.driver("bolt://neo4j:7687", auth=("neo4j", "***"))
COSINE_THRESHOLD = 0.82   # tuned per tenant, max 5 enrolled persons

def identify(tenant_id: str, crop: bytes):
    vec = embed_face(crop)                       # 512-d embedding
    with driver.session() as s:
        row = s.run("""
          MATCH (t:Tenant {id:$tid})-[:KNOWS]->(p:Person)
          WITH p, gds.similarity.cosine(p.embedding, $vec) AS sim
          ORDER BY sim DESC LIMIT 1
          RETURN p.name AS name, sim""", tid=tenant_id, vec=vec.tolist()).single()
    if row and row["sim"] >= COSINE_THRESHOLD:
        return {"person": row["name"], "confidence": round(row["sim"], 3)}
    return {"person": "unknown", "confidence": float(row["sim"]) if row else 0.0}

def record_event(tenant_id: str, person: str, obj: str, kind: str):
    with driver.session() as s:
        s.run("""
          MATCH (t:Tenant {id:$tid})-[:KNOWS]->(p:Person {name:$person})
          MERGE (o:Object {name:$obj, tenant:$tid})
          CREATE (e:Event {kind:$kind, at:datetime()})
          MERGE (p)-[:TRIGGERED]->(e)-[:LOCATED_AT]->(o)""",
          tid=tenant_id, person=person, obj=obj, kind=kind)`},
};

const compose = `version: "3.9"
services:
  gateway:            # FastAPI — multi-tenant auth (tenant_id JWT claim)
    build: ./gateway
    ports: ["8080:8080"]
    environment: [LLAMA_API_KEY, JWT_SECRET]
    depends_on: [redis, postgres]
  perception:         # SAM 3.1 via Meta Model API
    build: ./agents/perception
    environment: [LLAMA_API_KEY]
    deploy: { replicas: 3 }
  memory:             # Llama open-weights + Neo4j
    build: ./agents/memory
    environment: [NEO4J_PASSWORD]
    deploy: { replicas: 2 }
  spark-guard:        # Muse Spark — 30s fast loop
    build: ./agents/spark_guard
    environment: [LLAMA_API_KEY]
  wonder-reasoner:    # Muse Wonder — deep reasoning
    build: ./agents/wonder
    environment: [LLAMA_API_KEY, NEO4J_PASSWORD]
  image-alert:        # Muse Image — visual proof
    build: ./agents/image_alert
    environment: [LLAMA_API_KEY]
  news-agent:         # Wonder + web search
    build: ./agents/news
    environment: [LLAMA_API_KEY]
  neo4j:
    image: neo4j:5
    ports: ["7474:7474", "7687:7687"]
    environment: [NEO4J_AUTH]
    volumes: [neo4j_data:/data]
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
  postgres:
    image: postgres:16
    environment: [POSTGRES_PASSWORD]
    volumes: [pg_data:/var/lib/postgresql/data]
  pi-edge-simulator:  # replays Pi 5 telemetry for demos / CI
    build: ./simulator
    environment: [TENANT_ID=home-demo-01]
volumes: { neo4j_data: {}, pg_data: {} }`;

const cypher = `// Who triggered what, where — last hour, one tenant only
MATCH (t:Tenant {id: $tenant_id})-[:KNOWS]->(p:Person)
MATCH (p)-[:TRIGGERED]->(e:Event)-[:LOCATED_AT]->(o:Object)
WHERE e.at > datetime() - duration('PT1H')
OPTIONAL MATCH (e)-[:RELATED_TO_NEWS]->(n:NewsContext)
RETURN p.name, e.kind, o.name, e.at, n.headline
ORDER BY e.at DESC;`;

function CodeBlock({title, code, accent="#22d3ee"}:{title:string;code:string;accent?:string}){
  const [copied,setCopied]=useState(false);
  const copy=(e:React.MouseEvent)=>{
    const ta=document.createElement("textarea"); ta.value=code; document.body.appendChild(ta); ta.select();
    try{document.execCommand("copy"); setCopied(true); setTimeout(()=>setCopied(false),1600);}catch{}
    document.body.removeChild(ta);
    e.preventDefault();
  };
  return (
    <div className="rounded-xl overflow-hidden border" style={{borderColor:"var(--line)",background:"#05080f"}}>
      <div className="flex items-center justify-between px-4 py-2 border-b" style={{borderColor:"var(--line)",background:"var(--surface2)"}}>
        <span className="mono text-[11px] tracking-widest" style={{color:accent}}>{title}</span>
        <button onClick={copy} className="mono text-[11px] flex items-center gap-1 px-2 py-1 rounded border hover:opacity-80" style={{borderColor:"var(--line)",color:"var(--muted)"}}>{copied?<Check size={12}/> : <Copy size={12}/>}{copied?"COPIED":"COPY"}</button>
      </div>
      <pre className="mono text-[12px] leading-relaxed p-4 overflow-x-auto whitespace-pre" style={{color:"#c7d7f5"}}>{code}</pre>
    </div>
  );
}

function SectionHead({kicker,title,sub}:{kicker:string;title:string;sub:string}){
  return <div className="mb-6"><p className="mono text-[11px] tracking-[0.25em]" style={{color:"var(--accent)"}}>{kicker}</p><h2 className="text-2xl md:text-3xl font-bold mt-1">{title}</h2><p className="text-sm mt-2 max-w-3xl" style={{color:"var(--muted)"}}>{sub}</p></div>;
}

const tenants=[
  {id:"home-01",label:"Home 1 · Bengaluru",persons:5,events:1284,status:"● LIVE",color:"#34d399"},
  {id:"home-02",label:"Home 2 · Pune",persons:4,events:862,status:"● LIVE",color:"#34d399"},
  {id:"home-03",label:"Home 3 · Delhi",persons:5,events:1977,status:"◐ SYNCING",color:"#fbbf24"},
];

export default function App(){
  const [activeAgent,setActiveAgent]=useState(agents[2]);
  const [activeCode,setActiveCode]=useState<keyof typeof codeTabs>("spark");
  const [tenant,setTenant]=useState(tenants[0]);
  const [simStep,setSimStep]=useState(0);
  const [notes, setNotes] = useState<any[]>([]);
  const [noteText, setNoteText] = useState("");
  const [noteBusy, setNoteBusy] = useState(false);
  const [noteMsg, setNoteMsg] = useState("");
  async function apiCall(action: string, args: any) {
    const res = await fetch("actions", { method: "POST", headers: { "content-type": "application/json", ...(getToken() ? { "X-Admin-Token": getToken() } : {}) }, body: JSON.stringify({ action, args }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) throw new Error(data.error || "Request failed");
    return data.data;
  }
  async function reloadRecords() {
    const data = await apiCall("records.list", { collection: "notes" });
    setNotes(data?.records ?? []);
  }
  useEffect(() => { reloadRecords().catch(() => setNoteMsg("Notes will sync when the cloud API is connected.")); }, []);
  async function createRow(text: string) {
    return apiCall("records.create", { collection: "notes", value: { text, tenant_id: tenant.id, created_via: "drishti-console" } });
  }
  async function addNote() {
    const text = noteText.trim();
    if (!text || noteBusy) return;
    setNoteBusy(true);
    let saved = 0; let error: string | null = null;
    try { await createRow(text); saved += 1; } catch (e) { error = e instanceof Error ? e.message : String(e); }
    let reloadError: string | null = null;
    try { await reloadRecords(); } catch (e) { reloadError = e instanceof Error ? e.message : String(e); }
    if (saved) { setNoteText(""); setNoteMsg("Note saved to tenant workspace."); } else setNoteMsg(error || reloadError || "Could not save note.");
    setNoteBusy(false);
  }
  async function deleteNote(id: string) {
    try { await apiCall("records.delete", { collection: "notes", id }); await reloadRecords(); } catch { setNoteMsg("Delete failed — API unavailable."); }
  }
  const sim=[
    {t:"00:00",label:"PIR motion + camera frame captured on Pi 5",agent:"Edge"},
    {t:"00:01",label:"SAM 3.1 segments: person (unknown), stove ON, door open",agent:"Perception"},
    {t:"00:02",label:"Face embedding → no match in 5-person graph → 'unknown'",agent:"Memory"},
    {t:"00:03",label:"Spark Guard: risk 0.86 · intruder candidate",agent:"Spark Guard"},
    {t:"00:05",label:"News check: no power-cut / maintenance notice for area",agent:"News"},
    {t:"00:07",label:"Wonder: not a known habit (2:14 AM, weekday) → ACT",agent:"Wonder"},
    {t:"00:08",label:"Muse Image annotated proof + WhatsApp + Pi speaker warning",agent:"Action"},
  ];
  return (
  <div data-safe-area-inset-root style={{boxSizing:"border-box",paddingTop:"var(--safe-area-inset-top)"}} className="min-h-screen overflow-x-clip" >
    <div style={{background:"var(--bg)"}} className="min-h-screen">
    {/* HEADER */}
    <header data-safe-area-sticky-top style={{top:"var(--safe-area-inset-top)"}} className="sticky z-40 backdrop-blur border-b" >
      <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between" style={{background:"rgba(7,11,18,0.92)",borderColor:"var(--line)"}}>
        <div className="flex items-center gap-3"><div className="w-9 h-9 rounded-lg grid place-items-center font-bold" style={{background:"var(--accent)",color:"#04121a"}}><Eye size={20}/></div>
        <div><p className="font-bold leading-none">DRISHTI CLOUD</p><p className="mono text-[10px] tracking-widest" style={{color:"var(--muted)"}}>HOME SENTINEL · META GLOBAL AI HACKATHON</p></div></div>
        <nav className="hidden md:flex gap-5 mono text-[11px] tracking-widest" style={{color:"var(--muted)"}}>
          <a href="#arch" className="hover:text-white">ARCHITECTURE</a><a href="#tenancy" className="hover:text-white">TENANCY</a><a href="#multimodal" className="hover:text-white">MULTI-MODAL</a><a href="#graph" className="hover:text-white">NEO4J</a><a href="#code" className="hover:text-white">CODE</a><a href="#deploy" className="hover:text-white">DEPLOY</a>
        </nav>
        <span className="mono text-[10px] px-2 py-1 rounded-full border" style={{borderColor:"#14532d",color:"#4ade80",background:"#052e16"}}>SYSTEM DESIGN v2.1</span>
      </div>
    </header>

    <main className="max-w-7xl mx-auto px-4">
      {/* HERO */}
      <section className="py-10 md:py-14">
        <p className="mono text-[11px] tracking-[0.3em]" style={{color:"var(--accent)"}}>EYE → GRAPH → REASON → ACT · IN UNDER 8 SECONDS</p>
        <h1 className="text-4xl md:text-6xl font-bold leading-[1.02] mt-3 max-w-4xl">Drishti Cloud —<br/><span style={{color:"var(--accent)"}}>Multi-Agent Superintelligence</span><br/>for every home.</h1>
        <p className="mt-4 max-w-2xl text-base" style={{color:"var(--muted)"}}>A multi-agent, multi-tenant, multi-modal sentinel cloud. Raspberry Pi 5 homes stream compressed scene graphs — seven specialised agents perceive, remember, reason and act, grounded in a Neo4j knowledge graph and live world context.</p>
        <div className="flex flex-wrap gap-2 mt-6">{badges.map(b=><span key={b} className="mono text-[11px] px-3 py-1.5 rounded-full border" style={{borderColor:"var(--line)",background:"var(--surface)",color:"#c7d7f5"}}>{b}</span>)}</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-8">
          {[["7","Specialised agents"],["30 s","Spark Guard cadence"],["<8 s","Edge → action latency"],["1,000s","Homes per cluster"]].map(([v,l])=>(<div key={l} className="rounded-xl p-4 border" style={{background:"var(--surface)",borderColor:"var(--line)"}}><p className="text-3xl font-bold" style={{color:"var(--accent)"}}>{v}</p><p className="mono text-[11px] tracking-widest mt-1" style={{color:"var(--muted)"}}>{l}</p></div>))}
        </div>
      </section>

      {/* 2 ARCHITECTURE */}
      <section id="arch" className="py-8">
        <SectionHead kicker="01 · SYSTEM TOPOLOGY" title="Architecture — Edge to Cloud" sub="Each home is an autonomous edge tenant. Raw video never leaves the house — only a compressed scene-graph JSON crosses MQTT."/>
        <div className="grid lg:grid-cols-3 gap-4">
          <div className="rounded-2xl border p-5" style={{background:"var(--surface)",borderColor:"#164e63"}}>
            <p className="mono text-[11px] tracking-widest flex items-center gap-2" style={{color:"var(--accent)"}}><Home size={14}/> EDGE LAYER · PER TENANT</p>
            <div className="mt-4 space-y-2">
              {tenants.map(t=>(<button key={t.id} onClick={()=>setTenant(t)} className="w-full text-left rounded-lg px-3 py-2 border flex justify-between items-center" style={{background:tenant.id===t.id?"var(--surface2)":"transparent",borderColor:tenant.id===t.id?"var(--accent)":"var(--line)"}}><span className="text-sm font-semibold">{t.label}</span><span className="mono text-[10px]" style={{color:t.color}}>{t.status}</span></button>))}
            </div>
            <p className="mono text-[11px] mt-4 mb-2" style={{color:"var(--muted)"}}>RASPBERRY PI 5 · SENSOR RIG</p>
            <div className="grid grid-cols-2 gap-2 text-[12px]">
              {[["Camera","Pi Cam 3 · 1080p"],[ "HC-SR04 ×2","Ultrasonic distance"],["PIR","Motion"],["IR","Proximity gesture"],["DHT11","Temp / humidity"],["OLED","Status display"],["USB Mic","Audio in"],["2\" Speaker + PAM8403","Voice out"],["ISD1820","Hardware voice alarm"]].map(([a,b])=>(<div key={a} className="rounded-lg p-2 border" style={{borderColor:"var(--line)",background:"#05080f"}}><p className="font-semibold">{a}</p><p style={{color:"var(--muted)"}}>{b}</p></div>))}
            </div>
            <div className="mt-4 rounded-lg p-3 border border-dashed" style={{borderColor:"var(--accent)"}}>
              <p className="mono text-[11px]" style={{color:"var(--accent)"}}>EDGE AGENT (ON PI)</p>
              <p className="text-[12px] mt-1" style={{color:"var(--muted)"}}>Perception compression → motion-gated frames, downscaled crops, sensor fusion → <span className="mono" style={{color:"#c7d7f5"}}>scene-graph JSON via MQTT</span> (QoS 1, TLS, tenant topic <span className="mono">home/{tenant.id}/scene</span>)</p>
            </div>
          </div>

          <div className="lg:col-span-2 rounded-2xl border p-5" style={{background:"var(--surface)",borderColor:"var(--line)"}}>
            <p className="mono text-[11px] tracking-widest flex items-center gap-2" style={{color:"var(--accent2)"}}><Cloud size={14}/> CLOUD LAYER · DOCKER COMPOSE</p>
            <div className="rounded-lg p-3 mt-4 flex items-center justify-between border" style={{borderColor:"var(--line)",background:"#05080f"}}>
              <div><p className="font-bold text-sm">API Gateway · FastAPI</p><p className="mono text-[11px]" style={{color:"var(--muted)"}}>Multi-tenant auth — tenant_id from JWT claim on every request</p></div><Shield size={18} style={{color:"var(--accent)"}}/>
            </div>
            <div className="grid md:grid-cols-2 gap-2 mt-3">
              {agents.map(a=>{const Icon=a.icon; const active=activeAgent.id===a.id; return (
                <button key={a.id} onClick={()=>setActiveAgent(a)} className="text-left rounded-xl p-3 border transition" style={{borderColor:active?a.color:"var(--line)",background:active?"var(--surface2)":"#05080f"}}>
                  <p className="flex items-center gap-2 font-semibold text-sm"><Icon size={15} style={{color:a.color}}/>{a.name}</p>
                  <p className="mono text-[10px] mt-1" style={{color:a.color}}>{a.model}</p>
                </button>)})}
            </div>
            <div className="rounded-xl p-4 mt-3 border" style={{borderColor:activeAgent.color,background:"var(--surface2)"}}>
              <p className="font-bold flex items-center gap-2">{activeAgent.name}<span className="mono text-[10px] px-2 py-0.5 rounded-full border" style={{borderColor:activeAgent.color,color:activeAgent.color}}>SELECTED</span></p>
              <p className="text-sm mt-2" style={{color:"var(--muted)"}}>{activeAgent.desc}</p>
              <p className="mono text-[11px] mt-3 px-2 py-1 rounded" style={{background:"#05080f",color:"#c7d7f5"}}>{activeAgent.io}</p>
            </div>
            <div className="grid grid-cols-3 gap-2 mt-3 text-center">
              {[["Neo4j","Knowledge graph · habits, objects, persons, events",Database,"#34d399"],["Redis","Queue for multi-agent messages · pub/sub + streams",Radio,"#f87171"],["Postgres","Tenant data · timeline · audit log",Server,"#60a5fa"]].map(([n,d,Icon,c]:any)=>(<div key={n} className="rounded-xl p-3 border" style={{borderColor:"var(--line)",background:"#05080f"}}><Icon size={18} style={{color:c,margin:"0 auto"}}/><p className="font-bold text-sm mt-1">{n}</p><p className="text-[11px]" style={{color:"var(--muted)"}}>{d}</p></div>))}
            </div>
          </div>
        </div>

        {/* live sim */}
        <div className="rounded-2xl border p-5 mt-4" style={{background:"var(--surface)",borderColor:"var(--line)"}}>
          <div className="flex flex-wrap items-center justify-between gap-3"><p className="mono text-[11px] tracking-widest" style={{color:"var(--accent)"}}>LIVE TRACE · {tenant.label.toUpperCase()} · INTRUDER SCENARIO</p>
          <div className="flex gap-2"><button onClick={()=>setSimStep(s=>Math.max(0,s-1))} className="mono text-[11px] px-3 py-1.5 rounded border" style={{borderColor:"var(--line)"}}>← PREV</button><button onClick={()=>setSimStep(s=>Math.min(sim.length-1,s+1))} className="mono text-[11px] px-3 py-1.5 rounded" style={{background:"var(--accent)",color:"#04121a",fontWeight:700}}>STEP →</button><button onClick={()=>setSimStep(0)} className="mono text-[11px] px-3 py-1.5 rounded border" style={{borderColor:"var(--line)"}}>RESET</button></div></div>
          <div className="mt-4 space-y-2">{sim.map((s,i)=>(<div key={i} className="flex gap-3 items-start rounded-lg px-3 py-2 border" style={{opacity:i<=simStep?1:0.35,borderColor:i===simStep?"var(--accent)":"var(--line)",background:i===simStep?"var(--surface2)":"transparent"}}><span className="mono text-[11px]" style={{color:"var(--accent)"}}>{s.t}</span><span className="mono text-[10px] px-2 py-0.5 rounded-full border shrink-0" style={{borderColor:"var(--line)",color:"var(--muted)"}}>{s.agent}</span><span className="text-sm">{s.label}</span>{i===simStep&&<ChevronRight size={14} className="ml-auto shrink-0" style={{color:"var(--accent)"}}/>}</div>))}</div>
        </div>
      </section>

      {/* 3 TENANCY */}
      <section id="tenancy" className="py-8">
        <SectionHead kicker="02 · ISOLATION MODEL" title="Multi-Tenancy — Many Homes, Zero Leakage" sub="One cloud, thousands of homes. tenant_id is the partition key everywhere — graph, queues, storage and models."/>
        <div className="grid md:grid-cols-3 gap-4">
          {[
            [Network,"Neo4j subgraph isolation","Every node carries tenant:{id}. All Cypher is scoped MATCH (t:Tenant {id:$tid})-[:KNOWS]->… — a query physically cannot traverse into another home's subgraph. Face embeddings never co-mingle."],
            [Radio,"Redis queues = simultaneity","Per-tenant topics home/{tenant_id}/scene with consumer groups. Spark Guard replicas pull in parallel; a busy Home 3 never blocks Home 1. Back-pressure per tenant, fair-share scheduling."],
            [Shield,"Row + topic level isolation","Postgres RLS on tenant_id, MQTT ACLs per device certificate, JWT tenant claim verified at the FastAPI gateway. Each home sees only its timeline, its 5 persons, its habits."],
          ].map(([Icon,t,d]:any)=>(<div key={t} className="rounded-2xl border p-5" style={{background:"var(--surface)",borderColor:"var(--line)"}}><Icon size={20} style={{color:"var(--accent)"}}/><h3 className="font-bold mt-3">{t}</h3><p className="text-sm mt-2" style={{color:"var(--muted)"}}>{d}</p></div>))}
        </div>
        <div className="rounded-xl border p-4 mt-4 mono text-[12px] overflow-x-auto" style={{background:"#05080f",borderColor:"var(--line)",color:"#c7d7f5"}}>MQTT  home/home-01/scene ─┐<br/>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;home/home-02/scene ─┼→ Redis Streams → [Perception ×3] → [Spark ×N] → [Wonder ×2] → Action → MQTT home/{"{id}"}/actuate<br/>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;home/home-03/scene ─┘&nbsp;&nbsp;&nbsp;&nbsp;↑ per-tenant consumer groups · horizontal scale by adding replicas</div>
      </section>

      {/* 4 MULTIMODAL */}
      <section id="multimodal" className="py-8">
        <SectionHead kicker="03 · SENSOR FUSION" title="Multi-Modal Feed — One Scene Graph" sub="Six senses, one truth. Every modality lands in the same JSON the agents reason over."/>
        <div className="grid md:grid-cols-3 lg:grid-cols-6 gap-3">
          {[[Camera,"Video","SAM 3.1 segmentation","objects, masks, tracks"],[Mic,"Audio","Whisper STT","speech, glass-break, cry"],[Waves,"Ultrasound","HC-SR04 ×2","distance cm, approach vector"],[Activity,"Gesture","IR proximity","wave / reach events"],[Radio,"Motion","PIR","presence, zone triggers"],[Thermometer,"Climate","DHT11","temp °C, humidity %"]].map(([Icon,n,m,o]:any)=>(<div key={n} className="rounded-2xl border p-4 text-center" style={{background:"var(--surface)",borderColor:"var(--line)"}}><Icon size={22} style={{color:"var(--accent)",margin:"0 auto"}}/><p className="font-bold mt-2">{n}</p><p className="mono text-[10px]" style={{color:"var(--accent)"}}>{m}</p><p className="text-[11px] mt-1" style={{color:"var(--muted)"}}>{o}</p></div>))}
        </div>
        <div className="mt-4"><CodeBlock title="scene_graph.json — fused output (one message, every 30s or on event)" accent="#a78bfa" code={`{
  "tenant_id": "home-01", "ts": "2026-05-11T02:14:03+05:30",
  "vision":  { "objects": [{"label":"person","track":7,"bbox":[.12,.3,.3,.8]},
                            {"label":"stove","state":"on"}],
               "person": {"match":"unknown","conf":0.31} },
  "audio":   { "transcript": "", "events": ["glass_break:0.12"] },
  "range":   { "hc_sr04_a_cm": 142, "hc_sr04_b_cm": 210, "trend": "approaching" },
  "gesture": { "ir": "reach", "zone": "door" },
  "motion":  { "pir": true, "zone": "hall" },
  "climate": { "temp_c": 29.4, "humidity": 61 }
}`}/></div>
      </section>

      {/* 5 GRAPH */}
      <section id="graph" className="py-8">
        <SectionHead kicker="04 · KNOWLEDGE GRAPH" title="Neo4j Graph Schema" sub="The home's long-term memory. Habits, people, objects and world news — queryable in milliseconds, explainable in plain language."/>
        <div className="flex flex-wrap gap-2 mb-4">{["Tenant","Person","Object","Event","Habit","Alert","NewsContext"].map(n=><span key={n} className="mono text-[11px] px-3 py-1.5 rounded-full border font-semibold" style={{borderColor:"#365314",background:"#1a2e05",color:"#a3e635"}}>:{n}</span>)}
        {["KNOWS","LOCATED_AT","TRIGGERED","HABIT_OF","RELATED_TO_NEWS"].map(r=><span key={r} className="mono text-[11px] px-3 py-1.5 rounded-full border" style={{borderColor:"var(--line)",color:"var(--muted)"}}>-[{r}]-&gt;</span>)}</div>
        <div className="grid lg:grid-cols-2 gap-4">
          <CodeBlock title="schema.cypher" code={`CREATE CONSTRAINT tenant_id IF NOT EXISTS
  FOR (t:Tenant) REQUIRE t.id IS UNIQUE;
CREATE CONSTRAINT person_key IF NOT EXISTS
  FOR (p:Person) REQUIRE (p.tenant, p.name) IS UNIQUE;

// (Tenant)-[:KNOWS]->(Person)-[:TRIGGERED]->(Event)
// (Event)-[:LOCATED_AT]->(Object)
// (Person)-[:HABIT_OF]->(Habit)
// (Event)-[:RELATED_TO_NEWS]->(NewsContext)
// (Alert)-[:TRIGGERED]->(Event)`} accent="#a3e635"/>
          <CodeBlock title="query.cypher — explainable recall" code={cypher} accent="#a3e635"/>
        </div>
      </section>

      {/* 6 COMPOSE */}
      <section className="py-8">
        <SectionHead kicker="05 · INFRASTRUCTURE" title="Docker Compose — Whole Cloud, One Command" sub="Eleven services. Secrets via environment — LLAMA_API_KEY powers every Meta Model API call."/>
        <CodeBlock title="docker-compose.yml" code={compose} accent="#fbbf24"/>
      </section>

      {/* 7 CODE */}
      <section id="code" className="py-8">
        <SectionHead kicker="06 · AGENT CODE" title="Agent Implementations" sub="Meta models use the Responses API at api.meta.ai/v1/responses."/>
        <div className="flex flex-wrap gap-2 mb-4">{(Object.keys(codeTabs) as (keyof typeof codeTabs)[]).map(k=>(<button key={k} onClick={()=>setActiveCode(k)} className="mono text-[11px] px-3 py-2 rounded-lg border" style={{background:activeCode===k?"var(--accent)":"var(--surface)",color:activeCode===k?"#04121a":"var(--muted)",borderColor:"var(--line)",fontWeight:activeCode===k?700:400}}>{codeTabs[k].lang}</button>))}</div>
        <CodeBlock title={codeTabs[activeCode].lang} code={codeTabs[activeCode].code}/>
      </section>

      {/* 8 HACKATHON */}
      <section className="py-8">
        <SectionHead kicker="07 · HACKATHON FIT" title="How It Meets the Brief" sub="Built entirely on the Meta ecosystem — frontier Muse models for reasoning, SAM for perception, open weights where privacy demands it."/>
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
          {[["Muse Spark","Fast 30s guard loop via Meta Model API — sub-second safety verdicts.","#fbbf24"],["Muse Wonder","Deep chain-of-thought, habit learning, news-grounded decisions.","#a78bfa"],["Muse Image","Annotated visual proof for every alert.","#f472b6"],["SAM 3.1","Promptable segmentation turns frames into scene graphs.","#22d3ee"],["Open weights · Baseten / Modal","Llama face embeddings run locally / on-demand GPU — biometric data stays private.","#34d399"],["Muse Code","Agents scaffolded & iterated with Muse Code.","#60a5fa"],["Multi-agent","7 specialised agents on Redis queues, not one monolith prompt.","#fb923c"],["Multi-tenant","tenant_id isolation in Neo4j, Redis, Postgres & MQTT.","#38bdf8"],["Cloud-native","Docker Compose today, Modal/Baseten scale tomorrow.","#a3e635"]].map(([t,d,c])=>(<div key={t} className="rounded-2xl border p-4" style={{background:"var(--surface)",borderColor:"var(--line)"}}><p className="font-bold" style={{color:c}}>{t}</p><p className="text-sm mt-1" style={{color:"var(--muted)"}}>{d}</p></div>))}
        </div>
      </section>

      {/* 9 DEPLOY */}
      <section id="deploy" className="py-10">
        <SectionHead kicker="08 · DEPLOYMENT" title="Ship It" sub="From laptop demo to planet-scale sentinel in three moves."/>
        <div className="grid lg:grid-cols-2 gap-4">
          <CodeBlock title="terminal" accent="#34d399" code={`git clone https://github.com/drishti/drishti-cloud && cd drishti-cloud
cp .env.example .env        # set LLAMA_API_KEY, NEO4J_PASSWORD
docker compose up -d --build
docker compose ps           # 11 services healthy
# enrol a home (max 5 persons):
curl -X POST localhost:8080/tenants/home-01/persons \\
  -H "Authorization: Bearer $JWT" -d '{"name":"Asha"}'
open http://localhost:7474  # Neo4j Browser — watch the graph grow`}/>
          <div className="space-y-3">
            {[["1 · Compose (demo / pilot)","All agents + Neo4j + Redis + Postgres on one VM. pi-edge-simulator replays telemetry so judges see alerts without hardware."],["2 · Scale the hot agents","Perception & Spark Guard scale horizontally (stateless, Redis-fed). Wonder scales on demand — it only wakes on candidates."],["3 · Modal / Baseten (production)","GPU agents (SAM post-processing, Llama embeddings) move to Modal/Baseten autoscaling; Neo4j Aura + managed Redis/Postgres. Edge stays on Pi 5 — cloud cost per home stays in paise, not rupees."]].map(([t,d])=>(<div key={t} className="rounded-2xl border p-4" style={{background:"var(--surface)",borderColor:"var(--line)"}}><p className="font-bold">{t}</p><p className="text-sm mt-1" style={{color:"var(--muted)"}}>{d}</p></div>))}
          </div>
        </div>
        <div className="rounded-2xl border p-6 mt-6 text-center" style={{background:"linear-gradient(135deg,#0e2233,#1a1033)",borderColor:"var(--line)"}}>
          <Sparkles style={{color:"var(--accent)",margin:"0 auto"}}/>
          <p className="text-xl font-bold mt-2">Drishti sees. The graph remembers. Wonder reasons. The home acts.</p>
          <p className="mono text-[11px] tracking-widest mt-2" style={{color:"var(--muted)"}}>DRISHTI · HOME SENTINEL CLOUD · META GLOBAL AI HACKATHON</p>
        </div>
      </section>

      <section className="py-8">
        <SectionHead kicker="09 · OPERATOR WORKSPACE" title="Tenant Notes — Saved in the Cloud" sub="Durable operator notes per tenant, stored via the trusted records API. Add a deployment note and it persists across sessions."/>
        <div className="rounded-2xl border p-5" style={{background:"var(--surface)",borderColor:"var(--line)"}}>
          <div className="flex flex-col sm:flex-row gap-2">
            <input value={noteText} onChange={e=>setNoteText(e.target.value)} onKeyDown={e=>{if(e.key==="Enter") addNote();}} placeholder={`Note for ${tenant.label} — e.g. enrol Asha, tune Spark threshold…`} className="flex-1 rounded-lg px-3 py-2.5 text-sm border outline-none" style={{background:"#05080f",borderColor:"var(--line)",color:"var(--ink)"}} />
            <button onClick={addNote} disabled={noteBusy} className="mono text-[12px] px-4 py-2.5 rounded-lg font-bold disabled:opacity-50" style={{background:"var(--accent)",color:"#04121a"}}>{noteBusy?"SAVING…":"＋ SAVE NOTE"}</button>
          </div>
          {noteMsg && <p className="mono text-[11px] mt-3" style={{color:"var(--muted)"}}>{noteMsg}</p>}
          <div className="mt-4 space-y-2">
            {notes.length===0 ? <p className="text-sm" style={{color:"var(--muted)"}}>No saved notes yet for this workspace. Add one above — it will reload here on your next visit.</p> : notes.map((n:any)=>(<div key={n.id} className="flex items-start justify-between gap-3 rounded-lg px-3 py-2 border" style={{borderColor:"var(--line)",background:"#05080f"}}><div><p className="text-sm">{n.value?.text}</p><p className="mono text-[10px] mt-1" style={{color:"var(--muted)"}}>{n.value?.tenant_id} · {n.created_at ? new Date(n.created_at).toLocaleString() : ""}</p></div><button onClick={()=>deleteNote(n.id)} className="mono text-[11px] px-2 py-1 rounded border shrink-0" style={{borderColor:"var(--line)",color:"var(--muted)"}}>DELETE</button></div>))}
          </div>
        </div>
      </section>
    </main>
    <footer className="border-t py-6 text-center mono text-[11px]" style={{borderColor:"var(--line)",color:"var(--muted)"}}>Architecture document · Models: SAM 3.1 · Muse Spark / Wonder / Image · Llama open-weights · Infra: Neo4j · Redis · Postgres · Docker Compose</footer>
    </div>
  </div>);
}
