import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, DataQuality, NetworkTree, STATUS_LABEL, STATUSES, Status, Transformer } from "../api";
import { StatusLegend, statusColor } from "./status";

interface Props {
  tree: NetworkTree;
  onOpenNode: (nodeId: string) => void;
}

export function Overview({ tree, onOpenNode }: Props) {
  const [transformers, setTransformers] = useState<Transformer[] | null>(null);
  const [quality, setQuality] = useState<DataQuality | null>(null);

  useEffect(() => {
    api.transformers().then((r) => setTransformers(r.data));
    api.dataQuality().then(setQuality);
  }, []);

  const totals = Object.fromEntries(
    STATUSES.map((s) => [s, tree.zones.reduce((a, z) => a + z.meters_by_status[s], 0)]),
  ) as Record<Status, number>;
  const total = STATUSES.reduce((a, s) => a + totals[s], 0);
  const byZone = tree.zones.map((z) => ({ name: z.name, ...z.meters_by_status }));
  const worst = transformers
    ? [...transformers].sort((a, b) => b.meters_by_status.faulty / (b.meter_count || 1) - a.meters_by_status.faulty / (a.meter_count || 1))
    : [];
  const repairs = quality ? quality.issues.filter((i) => i.kind !== "code_reused").length : null;

  return (
    <div className="overview">
      <div className="kpis">
        <Kpi label="Meters" value={total} sub={`${tree.zones.length} zones · ${transformers?.length ?? "…"} transformers`} />
        {STATUSES.map((s) => (
          <Kpi key={s} label={STATUS_LABEL[s]} value={totals[s]} sub={`${((totals[s] / total) * 100).toFixed(0)} % of fleet`} color={statusColor(s)} />
        ))}
        <Kpi label="Record repairs" value={repairs ?? "…"} sub="blank or conflicting portal fields fixed" />
      </div>

      <div className="grid-2">
        <div className="card">
          <h3>Meter status by zone</h3>
          <StatusLegend />
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={byZone} layout="vertical" margin={{ top: 8, right: 8, left: 8, bottom: 0 }} barCategoryGap={10}>
              <CartesianGrid horizontal={false} stroke="var(--grid)" />
              <XAxis type="number" tick={{ fill: "var(--text-muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" width={110} tick={{ fill: "var(--text-secondary)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <Tooltip cursor={{ fill: "var(--surface-2)" }} contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }} />
              {STATUSES.map((s, i) => (
                <Bar key={s} dataKey={s} name={STATUS_LABEL[s]} stackId="a" fill={statusColor(s)} stroke="var(--surface-1)" strokeWidth={2}
                     radius={i === STATUSES.length - 1 ? [0, 4, 4, 0] : 0} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h3>Data quality: what the portal got wrong</h3>
          {!quality ? <p className="muted">Loading…</p> : (
            <table>
              <thead><tr><th>Problem</th><th style={{ textAlign: "right" }}>Count</th></tr></thead>
              <tbody>
                {Object.entries(quality.counts).sort((a, b) => b[1] - a[1]).map(([kind, n]) => (
                  <tr key={kind}><td>{KIND_LABEL[kind] ?? kind}</td><td style={{ textAlign: "right" }}>{n}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="muted" style={{ fontSize: 12 }}>
            Blank hierarchy fields are filled from other meters on the same transformer, and transformer names come from the
            transformer master list. Codes reused across branches are kept apart in the tree.
          </p>
        </div>
      </div>

      <div className="card">
        <h3>Transformers ranked by share of faulty meters</h3>
        {!transformers ? <p className="muted">Loading…</p> : (
          <table>
            <thead>
              <tr><th>Transformer</th><th>Feeder</th><th style={{ textAlign: "right" }}>Capacity</th><th style={{ textAlign: "right" }}>Meters</th><th style={{ width: "30%" }}>Faulty share</th></tr>
            </thead>
            <tbody>
              {worst.map((t) => {
                const share = t.meters_by_status.faulty / (t.meter_count || 1);
                return (
                  <tr key={t.code} className={t.node_id ? "clickable" : undefined} onClick={() => t.node_id && onOpenNode(t.node_id)}>
                    <td><strong>{t.code}</strong> <span className="secondary">{t.name}</span></td>
                    <td>{t.feeder_code}</td>
                    <td style={{ textAlign: "right" }}>{t.capacity_kva} kVA</td>
                    <td style={{ textAlign: "right" }}>{t.meter_count}</td>
                    <td>
                      <div className="share">
                        <div className="track"><div className="fill" style={{ width: `${share * 100}%` }} /></div>
                        <span style={{ width: 70, textAlign: "right" }}>{t.meters_by_status.faulty} ({(share * 100).toFixed(0)} %)</span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  blank: "Blank hierarchy code or name",
  conflict: "Conflicting value (e.g. stale transformer name)",
  code_reused: "Code reused under different parents",
  unknown_reference: "Transformer missing from master list",
  invalid: "Invalid or missing field",
  not_in_export: "Meter missing from bulk export",
};

function Kpi({ label, value, sub, color }: { label: string; value: number | string; sub: string; color?: string }) {
  return (
    <div className="card kpi">
      <div className="label">
        {color && <span style={{ color, marginRight: 6 }} aria-hidden>●</span>}
        {label}
      </div>
      <div className="value">{value}</div>
      <div className="sub">{sub}</div>
    </div>
  );
}
