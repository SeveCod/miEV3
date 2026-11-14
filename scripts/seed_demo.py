"""Crea una base de datos de demostración con 60 días de datos inventados (sin ubicación real).

Uso:  DB_PATH=demo.db venv/bin/python scripts/seed_demo.py
      DB_PATH=demo.db DASH_PASS=demo-miev3 venv/bin/python api.py   → http://127.0.0.1:8090 (admin / demo-miev3)
No toca nada si el fichero ya existe.
"""
import random, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config, db  # noqa: E402

if Path(config.DB_PATH).exists():
    sys.exit(f"{config.DB_PATH} ya existe; bórralo o usa otro DB_PATH")

rnd = random.Random(7)
db.init()
today = datetime.now().date()
odo, soc, total_wh = 4200.0, 80.0, 4200 * 150.0
snaps, trips, daily, charges = [], [], [], []
for i in range(60, -1, -1):
    day = today - timedelta(days=i)
    ymd = day.strftime("%Y%m%d")
    day_km = day_wh = 0.0
    for start_h in rnd.sample([7, 8, 13, 17, 18, 20], rnd.randint(1, 3)):
        km = round(rnd.uniform(4, 45), 1)
        mins = km / rnd.uniform(30, 80) * 60
        trips.append((ymd, f"{start_h:02d}{rnd.randint(0, 59):02d}00", km, round(mins), rnd.randint(0, 6),
                      round(km / mins * 60), round(km / mins * 60 * 1.6)))
        day_km += km
        day_wh += km * rnd.uniform(130, 170)
    if day_km:
        daily.append((ymd, day_wh, day_wh * .78, day_wh * .1, day_wh * .1, day_wh * .02, day_wh * .3, day_km))
    odo += day_km
    total_wh += day_wh
    # El contador del coche se queda corto: el % de batería baja ×1,2 lo que dice el contador
    soc -= day_wh * 1.2 / 1000 / config.BATTERY_KWH * 100
    ts = datetime(day.year, day.month, day.day, 21, tzinfo=timezone.utc)
    snaps.append((ts.isoformat(), round(soc, 1), 0, 0, 300 * soc / 100, odo, total_wh))
    if soc < 45:
        start, end = ts + timedelta(minutes=30), ts + timedelta(hours=7)
        kwh = (80 - soc) / 100 * config.BATTERY_KWH / config.CHARGE_EFFICIENCY
        charges.append((start.isoformat(), end.isoformat(), round(soc), 80, kwh, kwh * config.PRICE_EUR_KWH))
        snaps.append((start.isoformat(), round(soc, 1), 1, 1, 300 * soc / 100, odo, total_wh))  # cargando
        snaps.append((end.isoformat(), 80.0, 0, 1, 240, odo, total_wh))
        soc = 80.0

with db.conn() as c:
    c.executemany("INSERT INTO kia_snapshots (ts,soc,charging,plugged,range_km,odometer,total_consumed_wh) "
                  "VALUES (?,?,?,?,?,?,?)", snaps)
    c.execute("UPDATE kia_snapshots SET soh=100, battery_12v=90, charge_limit_ac=80, charge_limit_dc=90, "
              "locked=1, doors_open=0, windows_open=0, batt_temp_min=14, batt_temp_max=16, car_updated_at=ts")
    c.executemany(f"INSERT INTO trips ({','.join(db.TRIP_COLUMNS)}) VALUES (?,?,?,?,?,?,?)", trips)
    c.executemany(f"INSERT INTO daily_stats ({','.join(db.DAILY_COLUMNS)}) VALUES (?,?,?,?,?,?,?,?)", daily)
    c.executemany("INSERT INTO charge_sessions (start_ts,end_ts,soc_start,soc_end,kwh_est,cost_eur,source) "
                  "VALUES (?,?,?,?,?,?,'demo')", charges)
print(f"{config.DB_PATH}: {len(snaps)} lecturas, {len(trips)} viajes, {len(charges)} cargas")
