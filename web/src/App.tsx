import { useEffect, useMemo, useState } from "react";
import { api, MeterSummary, NetworkNode, NetworkTree } from "./api";
import { MeterMap } from "./components/MeterMap";
import { MeterPanel } from "./components/MeterPanel";
import { NetworkTree as Tree } from "./components/NetworkTree";
import { Overview } from "./components/Overview";

type Tab = "explorer" | "overview";

export default function App() {
  const [tab, setTab] = useState<Tab>("explorer");
  const [tree, setTree] = useState<NetworkTree | null>(null);
  const [meters, setMeters] = useState<MeterSummary[]>([]);
  const [nodeId, setNodeId] = useState<string | null>(null);
  const [nodeMeters, setNodeMeters] = useState<Set<string> | null>(null);
  const [meterId, setMeterId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.network(), api.meters()]).then(([t, m]) => {
      setTree(t);
      setMeters(m.data);
    }, (e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!nodeId) return setNodeMeters(null);
    api.nodeMeters(nodeId).then((r) => setNodeMeters(new Set(r.data.map((m) => m.id))));
  }, [nodeId]);

  const nodeName = useMemo(() => (tree && nodeId ? findNode(tree.zones, nodeId)?.name : null), [tree, nodeId]);
  const listed = nodeMeters ? meters.filter((m) => nodeMeters.has(m.id)) : [];

  const openNode = (id: string) => {
    setNodeId(id);
    setTab("explorer");
  };

  if (error) return <div className="error">Could not reach the API: {error}</div>;

  return (
    <div className="app">
      <header className="topbar">
        <h1>Urja Meter Explorer</h1>
        <div className="tabs" role="tablist">
          {(["explorer", "overview"] as Tab[]).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
              {t === "explorer" ? "Explorer" : "Overview"}
            </button>
          ))}
        </div>
        {tree && <span className="asof">Data as of {new Date(tree.data_as_of).toLocaleString()}</span>}
      </header>

      {!tree ? (
        <div className="panel muted">Loading network…</div>
      ) : tab === "overview" ? (
        <Overview tree={tree} onOpenNode={openNode} />
      ) : (
        <main className="explorer">
          <aside className="pane">
            <div className="pane-header">Network</div>
            <Tree zones={tree.zones} selectedId={nodeId} onSelect={(n: NetworkNode | null) => setNodeId(n?.id ?? null)} />
          </aside>
          <section className="map-pane" aria-label="Meter map">
            <MeterMap meters={meters} highlighted={nodeMeters} selectedId={meterId} onSelect={setMeterId} />
          </section>
          <aside className="pane">
            {meterId ? (
              <MeterPanel meterId={meterId} onSelectNode={(n) => setNodeId(n.id)} />
            ) : (
              <div className="panel">
                <h2>{nodeName ?? "All meters"}</h2>
                <p className="secondary">
                  {nodeName
                    ? `${listed.length} meters below this node. Pick one on the map or below.`
                    : "Pick a network node on the left, or a meter on the map."}
                </p>
                {listed.length > 0 && (
                  <table>
                    <thead><tr><th>Meter</th><th>Status</th><th>DT</th></tr></thead>
                    <tbody>
                      {listed.map((m) => (
                        <tr key={m.id} className="clickable" onClick={() => setMeterId(m.id)}>
                          <td>{m.id}</td><td>{m.status}</td><td>{m.transformer_code}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
            {meterId && (
              <div style={{ padding: "0 16px 16px" }}>
                <button className="link" onClick={() => setMeterId(null)}>
                  ← Back to {nodeName ?? "all meters"}
                </button>
              </div>
            )}
          </aside>
        </main>
      )}
    </div>
  );
}

function findNode(nodes: NetworkNode[], id: string): NetworkNode | undefined {
  for (const n of nodes) {
    if (n.id === id) return n;
    if (id.startsWith(n.id + ".")) return findNode(n.children, id);
  }
  return undefined;
}
