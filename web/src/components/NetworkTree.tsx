import { useState } from "react";
import { NetworkNode, STATUSES } from "../api";
import { statusColor } from "./status";

interface Props {
  zones: NetworkNode[];
  selectedId: string | null;
  onSelect: (node: NetworkNode | null) => void;
}

export function NetworkTree({ zones, selectedId, onSelect }: Props) {
  return (
    <ul className="tree" role="tree" aria-label="Network hierarchy">
      {zones.map((zone) => (
        <TreeItem key={zone.id} node={zone} selectedId={selectedId} onSelect={onSelect} />
      ))}
    </ul>
  );
}

function TreeItem({ node, selectedId, onSelect }: { node: NetworkNode } & Omit<Props, "zones">) {
  // Open the branch that contains the selection so it stays visible.
  const [open, setOpen] = useState(node.level === "zone");
  const expanded = open || (selectedId?.startsWith(node.id + ".") ?? false);
  const selected = node.id === selectedId;
  const hasChildren = node.children.length > 0;

  return (
    <li role="treeitem" aria-expanded={hasChildren ? expanded : undefined} aria-selected={selected}>
      <button
        className="tree-row"
        aria-current={selected}
        title={`${node.level} ${node.code}`}
        onClick={() => {
          onSelect(selected ? null : node);
          if (hasChildren) setOpen(true);
        }}
      >
        <span
          className="caret"
          onClick={(e) => {
            if (!hasChildren) return;
            e.stopPropagation();
            setOpen(!expanded);
          }}
        >
          {hasChildren ? (expanded ? "▾" : "▸") : ""}
        </span>
        <span className="label">
          {node.name}
          {node.name !== node.code && <small>{node.code}</small>}
        </span>
        <span className="count">{node.meter_count}</span>
      </button>
      <StatusBar counts={node.meters_by_status} total={node.meter_count} />
      {hasChildren && expanded && (
        <ul role="group">
          {node.children.map((child) => (
            <TreeItem key={child.id} node={child} selectedId={selectedId} onSelect={onSelect} />
          ))}
        </ul>
      )}
    </li>
  );
}

function StatusBar({ counts, total }: { counts: NetworkNode["meters_by_status"]; total: number }) {
  const label = STATUSES.map((s) => `${counts[s]} ${s}`).join(", ");
  return (
    <div className="statusbar" role="img" aria-label={label} title={label}>
      {STATUSES.filter((s) => counts[s] > 0).map((s) => (
        <span key={s} style={{ flex: counts[s] / total, background: statusColor(s) }} />
      ))}
    </div>
  );
}
