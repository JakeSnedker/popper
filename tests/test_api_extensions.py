from io import BytesIO
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from popper.api.extensions import install_extensions
from popper.artifacts import StrategySpec


def _record():
    spec = StrategySpec(
        id="S-1", hypothesis_id="H-1", name="test strategy",
        alpha={"features": ["atr_14", "ema_50"], "signal": "close > ema_50"},
    )
    return {
        "hypothesis": {"id": "H-1", "status": "promoted"},
        "specs": [spec.model_dump(mode="json")],
        "experiments": [{"id": "E-1", "spec_id": "S-1"}],
        "scorecards": [],
        "verdicts": [{"id": "V-1", "experiment_id": "E-1", "type": "promote",
                      "created_at": "2026-01-01T00:00:00+00:00"}],
    }


class Memory:
    def record(self, _hypothesis_id):
        return _record()


def _client(monkeypatch):
    monkeypatch.setattr("popper.security.auth.settings.auth_required", False)
    monkeypatch.setattr("popper.api.forge_router.get_memory", lambda: Memory())
    monkeypatch.setattr("popper.api.diagnostics_router.diagnostic_status", lambda value: {
        "hypothesis_id": value, "diagnostics": []})
    monkeypatch.setattr("popper.api.diagnostics_router.run_monte_carlo", lambda value: {
        "hypothesis_id": value, "name": "monte_carlo"})
    app = FastAPI()
    install_extensions(app)
    return TestClient(app)


def test_extension_routes_are_ready_for_one_line_main_install(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/api/auth/config").status_code == 200
    assert client.get("/api/hypotheses/H-1/diagnostics").status_code == 200
    assert client.post("/api/hypotheses/H-1/diagnostics/monte-carlo").status_code == 200
    manifest = client.get("/api/hypotheses/H-1/forge").json()
    assert {name.rsplit(".", 1)[-1] for name in manifest["files"]} >= {"pine", "mq5", "txt"}


def test_forge_bundle_download_is_a_real_zip(monkeypatch):
    response = _client(monkeypatch).get("/api/hypotheses/H-1/forge-download")
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert any(name.endswith(".pine") for name in names)
        assert any(name.endswith(".mq5") for name in names)
        assert "README.txt" in names


def test_fleet_endpoint_reports_nodes(monkeypatch):
    from popper import node_presence

    monkeypatch.setattr(node_presence, "fleet", lambda: [
        {"machine_id": "jake-beast", "status": "researching", "healthy": True},
        {"machine_id": "opb-work", "status": "stopped", "healthy": False},
    ])
    body = _client(monkeypatch).get("/api/fleet").json()
    assert body["healthy"] == 1
    assert [node["machine_id"] for node in body["nodes"]] == ["jake-beast", "opb-work"]
    assert body["error"] is None


def test_fleet_endpoint_degrades_instead_of_500(monkeypatch):
    from popper import node_presence

    def _boom():
        raise RuntimeError("supabase unreachable")

    monkeypatch.setattr(node_presence, "fleet", _boom)
    response = _client(monkeypatch).get("/api/fleet")
    assert response.status_code == 200
    assert response.json()["nodes"] == []
    assert "unreachable" in response.json()["error"]
