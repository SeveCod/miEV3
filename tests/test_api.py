"""Endpoints de eficiencia e histórico con una base de datos sembrada."""
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "DASH_USER", "admin")
    monkeypatch.setattr(config, "DASH_PASS", "x")
    monkeypatch.setattr(config, "DIESEL_KWH_PER_L", 10.0)
    monkeypatch.setattr(config, "WLTP_KWH_100", 14.9)
    import db
    db.init()
    with db.conn() as c:
        c.executemany("INSERT INTO daily_stats VALUES (?,?,?,?,?,?,?,?)", [
            ("20261009", 6000, 5000, 200, 700, 100, 4000, 50),
            ("20261010", 7200, 6000, 400, 800, 0, 6000, 60),
        ])
        c.executemany("INSERT INTO trips VALUES (?,?,?,?,?,?,?)", [
            ("20260930", "080000", 100, 60, 5, 90, 120),
            ("20261009", "080000", 30, 30, 2, 50, 90),
            ("20261009", "180000", 20, 25, 1, 40, 80),
            ("20261010", "120000", 60, 50, 3, 60, 130),
        ])
        c.execute("INSERT INTO kia_snapshots (ts,soc,odometer,total_consumed_wh) "
                  "VALUES ('2026-10-10T21:00:00+00:00',50,1000,120000)")
        c.execute("INSERT INTO charge_sessions (start_ts,end_ts,soc_start,soc_end,kwh_est,cost_eur,source) "
                  "VALUES ('2026-10-10T21:00:00+00:00','2026-10-11T03:00:00+00:00',47,80,21.4,2.74,'kia_soc')")
    import api
    from fastapi.testclient import TestClient
    return TestClient(api.app, headers={"Authorization": "Basic YWRtaW46eA=="})  # admin:x


def test_eficiencia(client):
    e = client.get("/api/efficiency").json()
    assert e["last_day"]["day"] == "20261010" and e["last_day"]["kwh_100"] == pytest.approx(12.0)
    # 30 días: (6000+7200) Wh / (50+60) km
    assert e["d30"]["kwh_100"] == pytest.approx(13200 / 110 / 10)
    assert e["lifetime"]["kwh_100"] == pytest.approx(12.0)  # 120000 Wh / 1000 km
    assert e["last_day"]["l_100"] == pytest.approx(1.2)    # 12 kWh/100 / 10 kWh por litro
    assert e["wltp_kwh_100"] == 14.9
    b = e["breakdown_30d"]
    assert (b["engine_wh"], b["climate_wh"], b["electronics_wh"], b["regen_wh"]) == (11000, 600, 1500, 10000)


def test_historico_por_mes(client):
    h = {r["period"]: r for r in client.get("/api/history?group=month").json()}
    assert h["2026-09"]["km"] == 100 and h["2026-09"]["kwh_100"] is None  # sin daily_stats ese mes
    assert h["2026-10"]["km"] == 110 and h["2026-10"]["trips"] == 3
    assert h["2026-10"]["kwh_100"] == pytest.approx(12.0)
    assert h["2026-10"]["charged_kwh"] == pytest.approx(21.4) and h["2026-10"]["cost_eur"] == pytest.approx(2.74)


def test_historico_por_dia_y_semana(client):
    d = {r["period"]: r for r in client.get("/api/history?group=day").json()}
    assert d["2026-10-09"]["km"] == 50 and d["2026-10-09"]["drive_min"] == 55
    w = client.get("/api/history?group=week").json()
    assert [r["period"] for r in w] == ["2026-W40", "2026-W41"]  # 30/09 y 09-10/10
    assert client.get("/api/history?group=year").status_code == 422


def test_sin_credenciales_401(client):
    from fastapi.testclient import TestClient
    import api
    assert TestClient(api.app).get("/api/efficiency").status_code == 401


def test_solo_se_sirve_img(client):
    assert client.get("/img/icon-car.png").status_code == 200
    assert client.get("/img/../catalogo.pdf").status_code == 404
    assert client.get("/static/catalogo.pdf").status_code == 404


def test_coste_y_autonomia_propia(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "PRICE_EUR_KWH", 0.128)
    monkeypatch.setattr(config, "CHARGE_EFFICIENCY", 0.9)
    monkeypatch.setattr(config, "BATTERY_KWH", 58.3)
    e = client.get("/api/efficiency").json()
    k30 = 13200 / 110 / 10
    assert e["d30"]["eur_100"] == pytest.approx(k30 / 0.9 * 0.128)
    assert e["d30"]["range_full_km"] == pytest.approx(58.3 / k30 * 100)
    m = {r["period"]: r for r in client.get("/api/history?group=month").json()}
    assert m["2026-10"]["cost_est_eur"] == pytest.approx(13.2 / 0.9 * 0.128)
    assert m["2026-09"]["cost_est_eur"] is None


def test_insights(client):
    i = client.get("/api/insights").json()
    assert i["records"]["longest_trip"]["distance_km"] == 100
    assert i["records"]["top_day"] == {"day": "20260930", "km": 100}
    assert i["records"]["max_speed"]["max_speed"] == 130
    assert i["records"]["best_day"]["day"] == "20261009"   # 12,0 vs 12,0 → empate; gana el primero
    # 210 km entre el 30/09 y el 10/10 (11 días) → proyección anual
    assert i["projection"]["km_per_day"] == pytest.approx(210 / 11)
    assert i["projection"]["km_year"] == pytest.approx(210 / 11 * 365)
    h = i["habits"]  # 7 días (lunes=0) × 24 horas, km
    assert len(h) == 7 and len(h[0]) == 24
    assert h[2][8] == 100          # 30/09/2026 es miércoles, 08 h
    assert h[4][8] == 30 and h[4][18] == 20   # 09/10/2026 es viernes


def test_status_incluye_carga_en_curso(client):
    import db
    with db.conn() as c:
        c.execute("UPDATE kia_snapshots SET charging=1")
    db.set_state("session_start_ts", "2026-10-10T22:46:57")
    db.set_state("session_start_soc", "46.5")
    s = client.get("/api/status").json()
    assert s["charging_since"] == {"ts": "2026-10-10T22:46:57", "soc": 46.5}
    with db.conn() as c:
        c.execute("UPDATE kia_snapshots SET charging=0")
    assert client.get("/api/status").json()["charging_since"] is None
