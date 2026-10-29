import { STATUS_ICON, STATUS_LABEL, Status } from "../api";

// Meter status is a state, so it uses the reserved status colours - always with an icon and label.
const COLORS: Record<Status, string> = {
  installed: "var(--status-good)",
  faulty: "var(--status-critical)",
  decommissioned: "var(--status-neutral)",
};

export const statusColor = (s: Status) => COLORS[s];

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className="badge">
      <span className="icon" style={{ color: COLORS[status] }} aria-hidden>
        {STATUS_ICON[status]}
      </span>
      {STATUS_LABEL[status]}
    </span>
  );
}

export function StatusLegend() {
  return (
    <div className="legend">
      {(Object.keys(COLORS) as Status[]).map((s) => (
        <span key={s} style={{ ["--c" as string]: COLORS[s] }}>
          {STATUS_LABEL[s]}
        </span>
      ))}
    </div>
  );
}
