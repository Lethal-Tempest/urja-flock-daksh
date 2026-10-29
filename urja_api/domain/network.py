"""Hierarchy from the per-meter export rows.

No hierarchy endpoint. Each meter carries its own copy of
zone > circle > division > subdivision > substation > feeder > transformer,
and the copies disagree: blank codes, a stale DT name, and codes like SD-01
that sit under more than one parent.

Once you ignore blanks, every DT has exactly one path, and /portal/dts
agrees on that DT's feeder. So the DT is the anchor and blanks get filled
from the other meters on it. Node ids are the full path (Z-01.C-01.D-01)
because the codes on their own aren't unique.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from urja_api.domain.models import LEVELS, DataIssue, NetworkLevel, NodeRef

# key for each level inside an export record's "hierarchy" object
EXPORT_KEYS: dict[NetworkLevel, str] = {
    NetworkLevel.zone: "zone",
    NetworkLevel.circle: "circle",
    NetworkLevel.division: "division",
    NetworkLevel.subdivision: "subdivision",
    NetworkLevel.substation: "substation",
    NetworkLevel.feeder: "feeder",
    NetworkLevel.transformer: "dt",
}
UNKNOWN = "UNKNOWN"


@dataclass
class Node:
    id: str
    level: NetworkLevel
    code: str
    name: str
    parent_id: str | None
    children: list[str] = field(default_factory=list)
    meter_ids: list[str] = field(default_factory=list)  # this node and everything under it

    def ref(self) -> NodeRef:
        return NodeRef(id=self.id, level=self.level, code=self.code, name=self.name)


@dataclass
class Network:
    nodes: dict[str, Node]
    meter_paths: dict[str, list[str]]  # meter id -> node ids, zone first
    transformer_nodes: dict[str, str]  # DT code -> node id
    issues: list[DataIssue]

    def path_refs(self, meter_id: str) -> list[NodeRef]:
        return [self.nodes[n].ref() for n in self.meter_paths.get(meter_id, [])]

    def roots(self) -> list[Node]:
        return sorted((n for n in self.nodes.values() if n.parent_id is None), key=lambda n: n.id)


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def build_network(meters: list[dict[str, Any]], transformers: list[dict[str, Any]]) -> Network:
    issues: list[DataIssue] = []
    master = {t["code"]: t for t in transformers}

    # (code, name) per level. The DT code is what we trust.
    raw: dict[str, dict[NetworkLevel, tuple[str, str]]] = {}
    by_dt: dict[str, list[str]] = defaultdict(list)
    for m in meters:
        hierarchy = m.get("hierarchy") or {}
        levels = {}
        for level, key in EXPORT_KEYS.items():
            node = hierarchy.get(key) or {}
            levels[level] = (_clean(node.get("code")), _clean(node.get("name")))
        dt_code = levels[NetworkLevel.transformer][0] or _clean(m.get("dtCode"))
        if not levels[NetworkLevel.transformer][0] and dt_code:
            levels[NetworkLevel.transformer] = (dt_code, levels[NetworkLevel.transformer][1])
        raw[m["meterId"]] = levels
        by_dt[dt_code or UNKNOWN].append(m["meterId"])

    # one code path per DT. majority wins, blanks get filled, dts list wins for feeder.
    codes: dict[str, list[str]] = {}
    for dt_code, meter_ids in by_dt.items():
        consensus: dict[NetworkLevel, str] = {}
        for level in LEVELS:
            seen = Counter(raw[mid][level][0] for mid in meter_ids if raw[mid][level][0])
            if level is NetworkLevel.feeder and dt_code in master:
                # /portal/dts is the one that knows which feeder a DT is on
                consensus[level] = master[dt_code]["feederCode"]
                if set(seen) - {consensus[level]}:
                    issues.append(DataIssue(
                        kind="conflict", field="feeder.code", problem=f"meters on {dt_code} report feeders {dict(seen)}",
                        action=f"using transformer master feeder {consensus[level]}"))
                continue
            if len(seen) > 1:
                issues.append(DataIssue(
                    kind="conflict", field=f"{level.value}.code", problem=f"meters on {dt_code} disagree: {dict(seen)}",
                    action="using the most common value"))
            consensus[level] = seen.most_common(1)[0][0] if seen else UNKNOWN
        for mid in meter_ids:
            path = []
            for level in LEVELS:
                code = raw[mid][level][0]
                if not code:
                    issues.append(DataIssue(
                        kind="blank", meter_id=mid, field=f"{level.value}.code", problem="blank",
                        action=f"filled with {consensus[level]} from other meters on {dt_code}"))
                elif code != consensus[level]:
                    issues.append(DataIssue(
                        kind="conflict", meter_id=mid, field=f"{level.value}.code", problem=f"{code} disagrees with other meters on {dt_code}",
                        action=f"replaced with {consensus[level]}"))
                path.append(consensus[level])
            codes[mid] = path
        if dt_code not in master and dt_code != UNKNOWN:
            issues.append(DataIssue(
                kind="unknown_reference", field="transformer.code", problem=f"{dt_code} is not in the transformer master list",
                action="kept; capacity and feeder unknown"))

    # names: most common non-blank one. transformer list overrides for DTs.
    names: dict[str, Counter] = defaultdict(Counter)
    names_by_code: dict[tuple[NetworkLevel, str], Counter] = defaultdict(Counter)
    for mid, path in codes.items():
        for depth, level in enumerate(LEVELS):
            node_id = ".".join(path[: depth + 1])
            name = raw[mid][level][1]
            if name:
                names[node_id][name] += 1
                names_by_code[(level, path[depth])][name] += 1

    def canonical_name(node_id: str, level: NetworkLevel, code: str) -> str:
        if level is NetworkLevel.transformer and code in master:
            return master[code]["name"]
        if names[node_id]:
            return names[node_id].most_common(1)[0][0]
        if names_by_code[(level, code)]:
            return names_by_code[(level, code)].most_common(1)[0][0]
        return code

    # build the tree
    nodes: dict[str, Node] = {}
    meter_paths: dict[str, list[str]] = {}
    for mid, path in sorted(codes.items()):
        ids = []
        parent = None
        for depth, level in enumerate(LEVELS):
            node_id = ".".join(path[: depth + 1])
            node = nodes.get(node_id)
            if node is None:
                node = Node(node_id, level, path[depth], canonical_name(node_id, level, path[depth]), parent)
                nodes[node_id] = node
                if parent:
                    nodes[parent].children.append(node_id)
            node.meter_ids.append(mid)
            raw_name = raw[mid][level][1]
            if not raw_name:
                issues.append(DataIssue(kind="blank", meter_id=mid, field=f"{level.value}.name", problem="blank",
                                        action=f"filled with '{node.name}'"))
            elif raw_name != node.name:
                issues.append(DataIssue(kind="conflict", meter_id=mid, field=f"{level.value}.name",
                                        problem=f"'{raw_name}' differs from the canonical name",
                                        action=f"using '{node.name}'"))
            ids.append(node_id)
            parent = node_id
        meter_paths[mid] = ids

    for node in nodes.values():
        node.children.sort()

    # codes the portal hangs under more than one parent
    parents_by_code: dict[tuple[NetworkLevel, str], set[str]] = defaultdict(set)
    for node in nodes.values():
        if node.parent_id:
            parents_by_code[(node.level, node.code)].add(nodes[node.parent_id].code)
    for (level, code), parents in sorted(parents_by_code.items()):
        if len(parents) > 1:
            issues.append(DataIssue(
                kind="code_reused", field=f"{level.value}.code",
                problem=f"{code} appears under {len(parents)} different parents ({', '.join(sorted(parents))})",
                action="kept as separate tree nodes (ids are path-qualified); filter by code to see them together"))

    transformer_nodes = {n.code: n.id for n in nodes.values() if n.level is NetworkLevel.transformer}
    return Network(nodes, meter_paths, transformer_nodes, issues)
