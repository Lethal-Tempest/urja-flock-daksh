// Typed access to the Urja Meter API. Shapes mirror openapi.json at the repo root.

export type Status = "installed" | "faulty" | "decommissioned";
export type Level = "zone" | "circle" | "division" | "subdivision" | "substation" | "feeder" | "transformer";

export interface Location { latitude: number; longitude: number }
export interface NodeRef { id: string; level: Level; code: string; name: string }
export interface DataIssue { kind: string; meter_id: string | null; field: string; problem: string; action: string }

export interface MeterSummary {
  id: string;
  serial_number: string;
  make: string;
  phase: "single" | "three";
  status: Status;
  installation_type: "whole_current" | "ct_operated";
  transformer_code: string;
  location: Location | null;
}
export interface Meter extends MeterSummary { network: NodeRef[]; data_issues: DataIssue[] }
export interface MeterPage { meta: { total: number; data_as_of: string }; data: MeterSummary[] }

export interface NetworkNode {
  id: string; level: Level; code: string; name: string;
  meter_count: number; meters_by_status: Record<Status, number>; children: NetworkNode[];
}
export interface NetworkTree { data_as_of: string; zones: NetworkNode[] }

export interface Transformer {
  code: string; name: string; capacity_kva: number; feeder_code: string; node_id: string | null;
  meter_count: number; meters_by_status: Record<Status, number>;
}

export interface Reading {
  timestamp: string; energy_kwh: number; apparent_energy_kvah: number; voltage_r: number | null; consumed_kwh: number | null;
}
export interface Consumption {
  meter_id: string; start: string | null; end: string | null;
  summary: {
    readings: number; interval_minutes: number | null; first_reading_at: string | null; last_reading_at: string | null;
    consumed_kwh: number | null; power_factor: number | null;
    voltage_min: number | null; voltage_avg: number | null; voltage_max: number | null; missing_intervals: number;
  };
  readings: Reading[];
}
export interface DataQuality { data_as_of: string; counts: Record<string, number>; issues: DataIssue[] }

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ message: response.statusText }));
    throw new Error(body.message ?? `HTTP ${response.status}`);
  }
  return response.json();
}

export const api = {
  meters: () => get<MeterPage>("/v1/meters?page_size=500"),
  meter: (id: string) => get<Meter>(`/v1/meters/${encodeURIComponent(id)}`),
  consumption: (id: string, start?: string, end?: string) => {
    const q = new URLSearchParams();
    if (start) q.set("start", start);
    if (end) q.set("end", end);
    return get<Consumption>(`/v1/meters/${encodeURIComponent(id)}/consumption?${q}`);
  },
  network: () => get<NetworkTree>("/v1/network"),
  nodeMeters: (nodeId: string) => get<MeterPage>(`/v1/meters?page_size=500&node=${encodeURIComponent(nodeId)}`),
  transformers: () => get<{ data: Transformer[] }>("/v1/transformers"),
  dataQuality: () => get<DataQuality>("/v1/data-quality"),
};

export const STATUS_LABEL: Record<Status, string> = { installed: "Installed", faulty: "Faulty", decommissioned: "Decommissioned" };
export const STATUS_ICON: Record<Status, string> = { installed: "●", faulty: "▲", decommissioned: "■" };
export const STATUSES: Status[] = ["installed", "faulty", "decommissioned"];
