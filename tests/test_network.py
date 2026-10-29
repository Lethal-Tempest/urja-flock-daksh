import copy

from tests.conftest import fixture
from urja_api.domain.models import NetworkLevel
from urja_api.domain.network import build_network


def build(meters=None):
    return build_network(meters or fixture("export.json")["data"], fixture("transformers.json"))


def test_every_meter_gets_a_full_path_without_blanks():
    network = build()
    for meter_id, path in network.meter_paths.items():
        assert len(path) == 7
        assert all(network.nodes[n].code and network.nodes[n].name for n in path), meter_id


def test_blank_codes_are_filled_from_the_same_transformer():
    network = build()
    blanks = [i for i in network.issues if i.kind == "blank" and i.field == "feeder.code"]
    assert blanks, "fixture contains meters with a blank feeder code"
    for issue in blanks:
        path = network.meter_paths[issue.meter_id]
        siblings = [p for p in network.meter_paths.values() if p[-1] == path[-1]]
        assert all(p == path for p in siblings)  # same transformer => identical repaired path


def test_stale_transformer_name_uses_master_list():
    network = build()
    dt7 = network.nodes[network.transformer_nodes["DT-007"]]
    assert dt7.name == "Sanganer DT 7"
    renamed = [i for i in network.issues if i.kind == "conflict" and i.field == "transformer.name"]
    assert {i.meter_id for i in renamed} == {"J100400", "J100401", "J100402"}


def test_reused_feeder_code_is_reported_and_kept_as_separate_nodes():
    network = build()
    f001 = [n for n in network.nodes.values() if n.level is NetworkLevel.feeder and n.code == "F-001"]
    assert len(f001) == 2  # DT-001 and DT-029 both hang off F-001, under different substations
    assert any(i.kind == "code_reused" and "F-001" in i.problem for i in network.issues)


def test_meter_counts_roll_up():
    network = build()
    total = sum(len(root.meter_ids) for root in network.roots())
    assert total == len(fixture("export.json")["data"])


def test_conflicting_code_takes_majority():
    meters = copy.deepcopy(fixture("export.json")["data"])
    victim = next(m for m in meters if m["dtCode"] == "DT-007")
    victim["hierarchy"]["division"]["code"] = "D-99"
    network = build(meters)
    path = network.meter_paths[victim["meterId"]]
    assert network.nodes[path[2]].code != "D-99"
    assert any(i.kind == "conflict" and i.meter_id == victim["meterId"] for i in network.issues)
