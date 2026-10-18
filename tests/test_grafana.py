import time
import pytest
from velosonet.grafana import resolve_panel, unit_scale, last_value
from velosonet.errors import GrafanaError

def dash():
    P = lambda i, t, unit="bps": {"id": i, "title": t, "type": "stat", "fieldConfig": {"defaults": {"unit": unit}}}
    return {"panels": [P(139, "TEFE 1 - TORRE CLARO"), {"id": 1, "type": "row", "title": "Linha", "panels": [P(358, "TEFE 2 - CX D'AGUA SAO MIGUEL")]},
                       P(359, "TEFE 3 - TORRE TIM"), P(500, "COARI"), P(501, "TEFE 3 - TORRE TIM")]}
SPEC = dict(allowed_panel_ids=[139, 358, 359, 377, 378, 384], metric="agregado")

def test_resolve_by_title_including_nested_row():
    assert resolve_panel(dash(), dict(SPEC, panel_title="TEFE 1 - TORRE CLARO"))["id"] == 139
    assert resolve_panel(dash(), dict(SPEC, panel_title="Tefe 2 - CX D’AGUA SAO MIGUEL"))["id"] == 358   # acento/caixa tolerados
def test_ambiguous_title_rejected():
    with pytest.raises(GrafanaError): resolve_panel(dash(), dict(SPEC, panel_title="TEFE 3 - TORRE TIM"))
def test_panel_outside_expected_ids_rejected():
    with pytest.raises(GrafanaError): resolve_panel(dash(), dict(SPEC, panel_title="COARI"))
def test_title_id_disagreement_rejected():
    with pytest.raises(GrafanaError): resolve_panel(dash(), dict(SPEC, panel_title="TEFE 1 - TORRE CLARO", panel_id=358))
def test_unit_scale_from_panel_and_unknown():
    p = dash()["panels"][0]
    assert unit_scale(p, {})[0] == 1e-6
    assert unit_scale(p, {"scale": 2})[0] == 2.0
    with pytest.raises(GrafanaError): unit_scale({"fieldConfig": {"defaults": {"unit": "short"}}}, {})
def test_last_value_converts_and_skips_nulls():
    now = int(time.time() * 1000)
    fr = [{"data": {"values": [[now - 120000, now - 60000, now], [90e6, 333e6, None]]}}]
    assert abs(last_value(fr, SPEC, 1e-6)["mbps"] - 333.0) < 1e-9
def test_stale_value_rejected():
    old = int(time.time() * 1000) - 3_600_000
    with pytest.raises(GrafanaError): last_value([{"data": {"values": [[old], [1e6]]}}], SPEC, 1e-6)
