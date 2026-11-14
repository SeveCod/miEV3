"""Snapshot ampliado, estadísticas diarias y migración del esquema sin red (vehículo simulado)."""
import datetime as dt
import sqlite3

import pytest
from hyundai_kia_connect_api.Vehicle import DailyDrivingStats, Vehicle

OLD_SCHEMA = """CREATE TABLE kia_snapshots (
  ts TEXT PRIMARY KEY, soc REAL, charging INTEGER, plugged INTEGER,
  range_km REAL, odometer REAL, lat REAL, lon REAL, battery_12v REAL, car_updated_at TEXT);"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    from collectors import kia
    v = Vehicle(id="X")
    v.ev_battery_percentage = 47
    v.ev_battery_is_charging = True
    v.ev_battery_is_plugged_in = 1
    v.ev_driving_range = (203, 1)
    v.odometer = (1701.6, 1)
    v.location = (40.1, -3.7, dt.datetime.now())
    v.car_battery_percentage = 88
    v.last_updated_at = dt.datetime.now()
    v.ev_charging_power = 3.1
    v.ev_estimated_current_charge_duration = (410, "m")
    v.ev_charge_limits_ac, v.ev_charge_limits_dc = 80, 90
    v.ev_battery_soh_percentage = 100
    v.ev_battery_temperature_min = (24, "°C")
    v.ev_battery_temperature_max = (26, "°C")
    v.is_locked = True
    v.front_left_door_is_open = 0
    v.trunk_is_open = 0
    v.back_right_window_is_open = True
    v.power_consumption_30d = 104
    v.total_power_consumed, v.total_power_regenerated = 174450, 120001
    v.daily_stats = [DailyDrivingStats(date=dt.datetime(2026, 10, 10), total_consumed=7346,
                                       engine_consumption=6690, climate_consumption=56,
                                       onboard_electronics_consumption=600, battery_care_consumption=0,
                                       regenerated_energy=606, distance=60)]

    class VM:
        vehicles = {"X": v}
        def update_all_vehicles_with_cached_state(self): pass

    monkeypatch.setattr(kia, "_manager", lambda: VM())
    return v, kia


def test_snapshot_ampliado(env):
    _, kia = env
    s = kia.snapshot()
    assert s["charge_power_kw"] == 3.1 and s["charge_remaining_min"] == 410
    assert (s["charge_limit_ac"], s["charge_limit_dc"]) == (80, 90)
    assert s["soh"] == 100 and (s["batt_temp_min"], s["batt_temp_max"]) == (24, 26)
    assert s["locked"] == 1 and s["doors_open"] == 0 and s["windows_open"] == 1
    assert s["consumption_30d"] == 104
    assert (s["total_consumed_wh"], s["total_regen_wh"]) == (174450, 120001)


def test_aperturas_desconocidas_son_none(env):
    v, kia = env
    for a in ("front_left_door_is_open", "trunk_is_open", "back_right_window_is_open"):
        setattr(v, a, None)
    s = kia.snapshot()
    assert s["doors_open"] is None and s["windows_open"] is None


def test_daily_stats(env):
    v, kia = env
    assert kia.daily_stats(v) == [{"day": "20261010", "total_wh": 7346, "engine_wh": 6690,
                                  "climate_wh": 56, "electronics_wh": 600, "battery_care_wh": 0,
                                  "regen_wh": 606, "distance_km": 60}]


def test_migracion_conserva_filas_y_guarda_todo(env):
    import config
    import db
    import main
    v, kia = env
    with sqlite3.connect(config.DB_PATH) as c:
        c.executescript(OLD_SCHEMA)
        c.execute("INSERT INTO kia_snapshots VALUES ('2026-10-10T22:46:57',46.5,1,1,200,1701.6,0,0,88,'x')")
    db.init()
    db.init()  # idempotente
    s = kia.snapshot()
    main.save_snapshot(s)
    main.save_daily_stats(kia.daily_stats(v))
    main.save_daily_stats(kia.daily_stats(v))  # upsert, sin duplicados
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM kia_snapshots ORDER BY ts")]
        days = c.execute("SELECT COUNT(*) n, MAX(regen_wh) r FROM daily_stats").fetchone()
    assert len(rows) == 2 and rows[0]["soc"] == 46.5 and rows[0]["soh"] is None
    assert rows[0]["ts"] == "2026-10-10T20:46:57+00:00" and rows[1]["ts"].endswith("+00:00")
    assert rows[1]["charge_power_kw"] == 3.1 and rows[1]["windows_open"] == 1
    assert (days["n"], days["r"]) == (1, 606)


def test_clave_desconocida_no_pierde_el_snapshot(env, caplog):
    import config, db, main
    _, kia = env
    db.init()
    s = kia.snapshot() | {"nueva_clave": 1}
    main.save_snapshot(s)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM kia_snapshots").fetchone()[0] == 1
    assert "nueva_clave" in caplog.text
