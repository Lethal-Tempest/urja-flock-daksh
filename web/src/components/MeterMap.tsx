import { useEffect, useMemo } from "react";
import { CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from "react-leaflet";
import { LatLngBounds } from "leaflet";
import "leaflet/dist/leaflet.css";
import { MeterSummary, STATUS_LABEL } from "../api";
import { StatusLegend } from "./status";

const FILL: Record<MeterSummary["status"], string> = {
  installed: "#0ca30c",
  faulty: "#d03b3b",
  decommissioned: "#8d8c86",
};

interface Props {
  meters: MeterSummary[];
  highlighted: Set<string> | null; // meters in the selected network node, or null for all
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function MeterMap({ meters, highlighted, selectedId, onSelect }: Props) {
  const located = meters.filter((m) => m.location);
  const focus = useMemo(
    () => located.filter((m) => !highlighted || highlighted.has(m.id)),
    [located, highlighted],
  );

  return (
    <>
      <MapContainer center={[26.9, 75.8]} zoom={11} preferCanvas>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitTo meters={focus} />
        {located.map((m) => {
          const dimmed = highlighted !== null && !highlighted.has(m.id);
          const selected = m.id === selectedId;
          return (
            <CircleMarker
              key={m.id}
              center={[m.location!.latitude, m.location!.longitude]}
              radius={selected ? 9 : 6}
              pathOptions={{
                color: selected ? "#0b0b0b" : "#ffffff",
                weight: selected ? 3 : 2,
                fillColor: FILL[m.status],
                fillOpacity: dimmed ? 0.15 : 0.9,
                opacity: dimmed ? 0.2 : 1,
              }}
              eventHandlers={{ click: () => onSelect(m.id) }}
            >
              <Tooltip>
                <strong>{m.id}</strong> · {STATUS_LABEL[m.status]}
                <br />
                {m.make} · {m.transformer_code}
              </Tooltip>
            </CircleMarker>
          );
        })}
      </MapContainer>
      <div className="card" style={{ position: "absolute", left: 12, bottom: 12, zIndex: 500, padding: "6px 10px" }}>
        <StatusLegend />
      </div>
    </>
  );
}

function FitTo({ meters }: { meters: MeterSummary[] }) {
  const map = useMap();
  const key = meters.map((m) => m.id).join(",");
  useEffect(() => {
    if (!meters.length) return;
    const bounds = new LatLngBounds(meters.map((m) => [m.location!.latitude, m.location!.longitude]));
    map.fitBounds(bounds.pad(0.2), { maxZoom: 15, animate: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, map]);
  return null;
}
