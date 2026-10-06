import os, sys
import pytest
pytest.importorskip("fastapi"); pytest.importorskip("httpx")
os.environ.setdefault("DASHBOARD_TOKEN", "test-token")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def client():
    import vector_dashboard as d
    from fastapi.testclient import TestClient
    return TestClient(d.app)


def test_vida_inactive_by_default(client):
    import vector_life
    vector_life._VIDAS.clear()
    assert client.get("/api/vida").json() == {"activo": False}


def test_vida_snapshot_shape_and_nexus_panel(client):
    import vector_life
    vector_life._VIDAS.clear()
    rb = vector_life.SimRobot()
    v = vector_life.Vida(rb, draw_eyes=False, sense_fn=lambda r, b: {
        "tof_mm": None, "pos_mm": (0, 0), "heading_deg": 0, "on_charger": False,
        "cliff": False, "picked_up": False, "battery_v": 4.0})
    vector_life._VIDAS[1] = v; v.step(0.05)
    j = client.get("/api/vida").json()
    for k in ("estado", "ruedas", "timeline", "mapa", "objetos", "personalidad"):
        assert k in j
    vector_life._VIDAS.clear()
    assert "card-vida" in client.get("/nexus").text


def test_post_endpoints_still_require_token(client):
    assert client.post("/api/decir", json={"texto": "hola"}).status_code in (401, 403)
