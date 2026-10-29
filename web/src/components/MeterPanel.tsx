import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, Consumption, Meter, NodeRef } from "../api";
import { StatusBadge } from "./status";

type Window = "7d" | "30d";
// Indian supply standard: 230 V ± 6 %.
const V_LOW = 216.2;
const V_HIGH = 243.8;

interface Props {
  meterId: string;
  onSelectNode: (node: NodeRef) => void;
}

export function MeterPanel({ meterId, onSelectNode }: Props) {
  const [meter, setMeter] = useState<Meter | null>(null);
  const [usage, setUsage] = useState<Consumption | null>(null);
  const [window, setWindow] = useState<Window>("7d");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setMeter(null);
    setError(null);
    api.meter(meterId).then(setMeter, (e) => setError(e.message));
  }, [meterId]);

  useEffect(() => {
    let cancelled = false;
    setUsage(null);
    (async () => {
      // The portal's data has a fixed end date, so anchor the window on the latest reading,
      // then ask for whole local days (the portal's own default window starts mid-day).
      const latest = await api.consumption(meterId);
      if (!latest.summary.last_reading_at) return latest;
      const end = lastFullDay(latest.summary.last_reading_at);
      return api.consumption(meterId, addDays(end, window === "7d" ? -6 : -29), end);
    })().then(
      (c) => !cancelled && setUsage(c),
      (e) => !cancelled && setError(e.message),
    );
    return () => {
      cancelled = true;
    };
  }, [meterId, window]);

  const daily = useMemo(() => (usage ? dailyUsage(usage) : []), [usage]);
  const profile = useMemo(() => (usage?.summary.interval_minutes === 30 ? loadProfile(usage) : null), [usage]);

  if (error) return <div className="error">Could not load {meterId}: {error}</div>;
  if (!meter) return <div className="panel muted">Loading {meterId}…</div>;
  const s = usage?.summary;

  return (
    <div className="panel">
      <div>
        <h2>Meter {meter.id}</h2>
        <nav className="breadcrumb" aria-label="Network position">
          {meter.network.map((n, i) => (
            <span key={n.id}>
              <button onClick={() => onSelectNode(n)} title={n.level}>{n.name}</button>
              {i < meter.network.length - 1 && <span className="muted"> ›</span>}
            </span>
          ))}
        </nav>
      </div>

      <dl className="kv">
        <dt>Status</dt><dd><StatusBadge status={meter.status} /></dd>
        <dt>Serial</dt><dd>{meter.serial_number}</dd>
        <dt>Make</dt><dd>{meter.make}</dd>
        <dt>Phase</dt><dd>{meter.phase === "single" ? "Single phase" : "Three phase"}</dd>
        <dt>Installation</dt><dd>{meter.installation_type === "ct_operated" ? "CT operated" : "Whole current"}</dd>
        <dt>Location</dt>
        <dd>{meter.location ? `${meter.location.latitude.toFixed(5)}, ${meter.location.longitude.toFixed(5)}` : "Unknown"}</dd>
      </dl>

      {s && <Alerts meter={meter} usage={usage!} />}

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <h3 style={{ margin: 0 }}>Daily consumption (kWh)</h3>
          <div className="chips" role="group" aria-label="Window">
            {(["7d", "30d"] as Window[]).map((w) => (
              <button key={w} aria-pressed={window === w} onClick={() => setWindow(w)}>
                {w === "7d" ? "7 days" : "30 days"}
              </button>
            ))}
          </div>
        </div>
        {!usage ? (
          <p className="muted">Loading readings…</p>
        ) : daily.length === 0 ? (
          <p className="muted">No readings in this window.</p>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={daily} margin={{ top: 4, right: 4, bottom: 0, left: -16 }} barCategoryGap={2}>
                <CartesianGrid vertical={false} stroke="var(--grid)" />
                <XAxis dataKey="label" tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                <YAxis tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickLine={false} axisLine={false} width={48} />
                <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTip unit="kWh" />} />
                <Bar dataKey="kwh" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={28} />
              </BarChart>
            </ResponsiveContainer>
            <div className="stats" style={{ marginTop: 8 }}>
              <Stat label="Total" value={s?.consumed_kwh != null ? `${s.consumed_kwh.toFixed(1)} kWh` : "—"} />
              <Stat label="Per day" value={`${(daily.reduce((a, d) => a + d.kwh, 0) / daily.length).toFixed(1)} kWh`} />
              <Stat label="Power factor" value={s?.power_factor?.toFixed(3) ?? "—"} />
              <Stat label="Voltage" value={s?.voltage_avg != null ? `${s.voltage_min}–${s.voltage_max} V` : "—"} />
            </div>
            <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>
              {s?.interval_minutes === 30 ? "Half-hourly" : "Daily"} readings · {s?.readings} in window
            </p>
          </>
        )}
      </div>

      {profile && (
        <div className="card">
          <h3>Average day (kWh per half hour)</h3>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={profile} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis dataKey="slot" tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickLine={false} axisLine={false} interval={7} />
              <YAxis tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickLine={false} axisLine={false} width={48} />
              <Tooltip cursor={{ stroke: "var(--text-muted)", strokeDasharray: "3 3" }} content={<ChartTip unit="kWh" />} />
              <Line type="monotone" dataKey="kwh" stroke="var(--series-1)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {meter.data_issues.length > 0 && (
        <div className="card">
          <h3>Portal record repairs</h3>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {meter.data_issues.map((i, k) => (
              <li key={k}><code>{i.field}</code> {i.problem} → <span className="secondary">{i.action}</span></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Alerts({ meter, usage }: { meter: Meter; usage: Consumption }) {
  const s = usage.summary;
  const alerts: { icon: string; color: string; text: string }[] = [];
  if (meter.status === "decommissioned" && (s.consumed_kwh ?? 0) > 0)
    alerts.push({ icon: "▲", color: "var(--status-critical)", text: `Decommissioned, yet ${s.consumed_kwh!.toFixed(1)} kWh recorded in this window. The status may be stale, or supply is still live.` });
  if (meter.status === "faulty" && s.readings > 0 && s.missing_intervals === 0)
    alerts.push({ icon: "!", color: "var(--status-warning)", text: "Marked faulty, but reporting complete readings. The fault flag may need review." });
  if (s.voltage_min != null && (s.voltage_min < V_LOW || (s.voltage_max ?? 0) > V_HIGH))
    alerts.push({ icon: "!", color: "var(--status-warning)", text: `Voltage outside 230 V ± 6 % (${s.voltage_min}–${s.voltage_max} V).` });
  if (s.missing_intervals > 0)
    alerts.push({ icon: "!", color: "var(--status-warning)", text: `${s.missing_intervals} expected readings missing.` });
  if (!alerts.length) return null;
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {alerts.map((a, i) => (
        <div className="alert" key={i}>
          <span className="icon" style={{ color: a.color }} aria-hidden>{a.icon}</span>
          <span>{a.text}</span>
        </div>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <div className="value" style={{ fontSize: 15 }}>{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

function ChartTip({ active, payload, label, unit }: { active?: boolean; payload?: { value: number }[]; label?: string; unit: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <span className="muted">{label}</span>
      <strong>{payload[0].value.toFixed(2)} {unit}</strong>
    </div>
  );
}

// ---- data shaping -------------------------------------------------------------
// Timestamps carry the utility's local offset, so the date/time prefix *is* local time.

function intervalStart(ts: string, minutes: number): { date: string; time: string } {
  const [date, rest] = ts.split("T");
  const [h, m] = rest.slice(0, 5).split(":").map(Number);
  let total = h * 60 + m - minutes;
  let day = date;
  if (total < 0) {
    total += 1440;
    day = addDays(date, -1);
  }
  const hh = String(Math.floor(total / 60)).padStart(2, "0");
  const mm = String(total % 60).padStart(2, "0");
  return { date: day, time: `${hh}:${mm}` };
}

// Each reading's consumed_kwh covers the interval *ending* at its timestamp.
function dailyUsage(c: Consumption) {
  const step = c.summary.interval_minutes ?? 1440;
  const byDay = new Map<string, number>();
  for (const r of c.readings) {
    if (r.consumed_kwh == null) continue;
    const { date } = intervalStart(r.timestamp, step);
    byDay.set(date, (byDay.get(date) ?? 0) + r.consumed_kwh);
  }
  return [...byDay].map(([date, kwh]) => ({ date, label: date.slice(8) + "/" + date.slice(5, 7), kwh: +kwh.toFixed(2) }));
}

function loadProfile(c: Consumption) {
  const sums = new Map<string, { total: number; n: number }>();
  for (const r of c.readings) {
    if (r.consumed_kwh == null) continue;
    const { time } = intervalStart(r.timestamp, 30);
    const s = sums.get(time) ?? { total: 0, n: 0 };
    sums.set(time, { total: s.total + r.consumed_kwh, n: s.n + 1 });
  }
  return [...sums].sort(([a], [b]) => a.localeCompare(b)).map(([slot, s]) => ({ slot, kwh: +(s.total / s.n).toFixed(3) }));
}

function lastFullDay(lastReading: string): string {
  return intervalStart(lastReading, 1).date;
}

function addDays(date: string, days: number): string {
  const d = new Date(date + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
