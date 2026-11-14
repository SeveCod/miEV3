"""Consumo calibrado con el % de batería: el contador de Kia se queda corto (medido 2026-10-26:
165,6 kWh de batería frente a 127,7 kWh del contador en 1.105 km, ×1,30). Un único factor,
de toda la historia, multiplica todas las cifras de Kia; sin base suficiente, ×1."""
import pytest

KIA = 23.32 / 1.3 * 1000   # Wh que marca el contador por tramo: 30 % menos que la batería


def _seed(c, tramos):
    """Cada tramo: SoC 90→50 % (23,32 kWh con 58,3) en 200 km, sin cargar; entre tramos, una carga."""
    tc, km, h = 100000.0, 1000.0, 0
    for i in range(tramos):
        for soc, dkm, dtc in ((90, 0, 0), (70, 100, KIA / 2), (50, 100, KIA / 2)):
            tc += dtc; km += dkm; h += 1
            c.execute("INSERT INTO kia_snapshots (ts,soc,charging,odometer,total_consumed_wh) VALUES (?,?,0,?,?)",
                      (f"2026-10-{10 + h // 24:02d}T{h % 24:02d}:00:00+00:00", soc, km, tc))
        h += 1
        c.execute("INSERT INTO kia_snapshots (ts,soc,charging,odometer,total_consumed_wh) VALUES (?,?,1,?,?)",
                  (f"2026-10-{10 + h // 24:02d}T{h % 24:02d}:00:00+00:00", 60, km, tc))


def _client(tmp_path, monkeypatch, tramos):
    import config, db
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "DASH_USER", "admin"); monkeypatch.setattr(config, "DASH_PASS", "test-pass")
    monkeypatch.setattr(config, "BATTERY_KWH", 58.3)
    db.init()
    with db.conn() as c:
        c.executemany("INSERT INTO daily_stats VALUES (?,?,?,?,?,?,?,?)", [
            ("20261009", 6000, 5000, 200, 700, 100, 4000, 50),
            ("20261010", 7200, 6000, 400, 800, 0, 6000, 60),
        ])
        c.execute("INSERT INTO trips VALUES ('20261010','120000',60,50,3,60,130)")
        _seed(c, tramos)
    import api
    from fastapi.testclient import TestClient
    return TestClient(api.app, headers={"Authorization": "Basic YWRtaW46dGVzdC1wYXNz"})


def test_factor_calibrado_escala_todas_las_cifras(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, tramos=2)    # 400 km, 2 tramos
    e = client.get("/api/efficiency").json()
    assert e["calibrated"] and e["soc_factor"] == pytest.approx(1.3, abs=1e-3)
    assert e["soc_basis_km"] == pytest.approx(400) and e["soc_segments"] == 2
    assert e["last_day"]["kwh_100"] == pytest.approx(12.0 * 1.3, abs=1e-2)
    assert e["d30"]["kwh_100"] == pytest.approx(13200 / 110 / 10 * 1.3, abs=1e-2)
    assert e["breakdown_30d"]["regen_wh"] == 10000           # la regeneración no se toca
    h = {r["period"]: r for r in client.get("/api/history?group=day").json()}
    assert h["2026-10-10"]["kwh_100"] == pytest.approx(12.0 * 1.3, abs=1e-2)
    best = client.get("/api/insights").json()["records"]["best_day"]
    assert best["kwh_100"] == pytest.approx(12.0 * 1.3, abs=1e-2)


def test_sin_base_suficiente_no_calibra(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, tramos=1)    # 200 km: poca base
    e = client.get("/api/efficiency").json()
    assert not e["calibrated"] and e["soc_factor"] == 1.0
    assert e["last_day"]["kwh_100"] == pytest.approx(12.0)
