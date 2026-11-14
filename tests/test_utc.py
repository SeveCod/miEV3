"""Marcas de tiempo en UTC: el cambio de hora no pisa snapshots y lo antiguo se migra una sola vez."""
import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    import db
    return db


def test_cambio_de_hora_no_pisa_snapshots(fresh_db):
    """25/10/2026: las 02:30 se repiten (CEST y luego CET); en UTC son dos marcas distintas."""
    import main
    db = fresh_db
    db.init()
    main.save_snapshot({"ts": "2026-10-25T00:30:00+00:00", "soc": 50})  # 02:30 CEST
    main.save_snapshot({"ts": "2026-10-25T01:30:00+00:00", "soc": 49})  # 02:30 CET
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM kia_snapshots").fetchone()[0] == 2


def test_ahora_es_utc_con_offset(fresh_db):
    from datetime import datetime
    ts = fresh_db.utcnow()
    assert ts.endswith("+00:00") and datetime.fromisoformat(ts).utcoffset().total_seconds() == 0


def test_migra_hora_local_a_utc_una_sola_vez(fresh_db):
    db = fresh_db
    db.init()
    with db.conn() as c:
        c.execute("INSERT INTO kia_snapshots (ts, soc) VALUES ('2026-10-10T22:46:57', 46)")
        c.execute("INSERT INTO v2c_readings (ts) VALUES ('2026-10-10T12:00:00')")
        c.execute("INSERT INTO charge_sessions (start_ts, end_ts, source) "
                  "VALUES ('2026-10-10T23:00', '2026-10-11T05:00', 'kia_soc')")
    db.set_state("session_start_ts", "2026-10-11T01:15:00")
    db.init()
    db.init()  # idempotente: no vuelve a restar
    with db.conn() as c:
        assert c.execute("SELECT ts FROM kia_snapshots").fetchone()[0] == "2026-10-10T20:46:57+00:00"
        assert c.execute("SELECT ts FROM v2c_readings").fetchone()[0] == "2026-10-10T10:00:00+00:00"
        s = c.execute("SELECT start_ts, end_ts FROM charge_sessions").fetchone()
    assert (s[0], s[1]) == ("2026-10-10T21:00:00+00:00", "2026-10-11T03:00:00+00:00")
    assert db.get_state("session_start_ts") == "2026-10-10T23:15:00+00:00"


def test_historico_agrupa_cargas_por_dia_local(fresh_db, monkeypatch):
    """Una carga que termina a las 23:30 UTC del 31/10 es el 1/11 a las 00:30 en España."""
    import config
    monkeypatch.setattr(config, "DASH_USER", "admin"); monkeypatch.setattr(config, "DASH_PASS", "test-pass")
    db = fresh_db
    db.init()
    with db.conn() as c:
        c.execute("INSERT INTO charge_sessions (start_ts,end_ts,kwh_est,cost_eur,source) "
                  "VALUES ('2026-10-31T20:00:00+00:00','2026-10-31T23:30:00+00:00',10,1.2,'kia_soc')")
    import api
    from fastapi.testclient import TestClient
    cl = TestClient(api.app, headers={"Authorization": "Basic YWRtaW46dGVzdC1wYXNz"})
    d = {r["period"]: r for r in cl.get("/api/history?group=day").json()}
    assert d["2026-11-01"]["charged_kwh"] == 10
    m = {r["period"] for r in cl.get("/api/history?group=month").json()}
    assert m == {"2026-11"}


def test_snapshots_filtra_por_ventana_utc(fresh_db, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import config
    monkeypatch.setattr(config, "DASH_USER", "admin"); monkeypatch.setattr(config, "DASH_PASS", "test-pass")
    db = fresh_db
    db.init()
    now = datetime.now(timezone.utc)
    iso = lambda h: (now - timedelta(hours=h)).isoformat(timespec="seconds")
    with db.conn() as c:
        c.executemany("INSERT INTO kia_snapshots (ts, soc) VALUES (?, ?)",
                      [(iso(23), 1), (iso(25), 2)])
    import api
    from fastapi.testclient import TestClient
    cl = TestClient(api.app, headers={"Authorization": "Basic YWRtaW46dGVzdC1wYXNz"})
    assert [s["soc"] for s in cl.get("/api/snapshots?days=1").json()] == [1]
