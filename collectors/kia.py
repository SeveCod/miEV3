"""Lectura del coche con hyundai_kia_connect_api (Kia/Hyundai/Genesis; probado solo con Kia UE).
Usa SIEMPRE el estado en caché del servidor: no despierta el coche ni gasta la 12 V."""
import logging
from hyundai_kia_connect_api import VehicleManager
import config, db

_vm = None


def _manager():
    global _vm
    if _vm is None:
        _vm = VehicleManager(region=config.CAR_REGION, brand=config.CAR_BRAND,
                             username=config.KIA_USER, password=config.KIA_PASSWORD,
                             pin=config.KIA_PIN)
    _vm.check_and_refresh_token()
    return _vm


def _vehicle(vm):
    return next(iter(vm.vehicles.values()))


def _flag(x):
    return None if x is None else int(bool(x))


def _any_open(v, attrs):
    """1 si alguno está abierto, 0 si todos cerrados, None si el coche no informa de ninguno."""
    vals = [getattr(v, a, None) for a in attrs]
    known = [x for x in vals if x is not None]
    return int(any(known)) if known else None


DOORS = ("front_left_door_is_open", "front_right_door_is_open", "back_left_door_is_open",
         "back_right_door_is_open", "trunk_is_open", "hood_is_open")
# Ojo: en CCS2 una ventanilla en posición de ventilación puede leerse cerrada (api #1215)
WINDOWS = ("front_left_window_is_open", "front_right_window_is_open",
           "back_left_window_is_open", "back_right_window_is_open")


def read():
    """Vehículo con el estado en caché del servidor de Kia (no lo despierta)."""
    vm = _manager()
    vm.update_all_vehicles_with_cached_state()
    return _vehicle(vm)


def snapshot(v=None) -> dict:
    v = v or read()
    g = lambda a: getattr(v, a, None)
    upd = g("last_updated_at")
    return {
        "ts": db.utcnow(),
        "soc": g("ev_battery_percentage"),
        # None = desconocido (no confundir con 0). En coches CCS2 (p. ej. Kia EV3) la librería
        # (desde la 4.35) deduce "cargando" del tiempo restante de carga y puede dejarlo en None.
        # TODO verificar con una carga real en cada modelo.
        "charging": _flag(g("ev_battery_is_charging")),
        "plugged": _flag(g("ev_battery_is_plugged_in")),
        "range_km": g("ev_driving_range"),
        "odometer": g("odometer"),
        "lat": g("location_latitude"),
        "lon": g("location_longitude"),
        "battery_12v": g("car_battery_percentage"),
        "car_updated_at": upd.isoformat() if hasattr(upd, "isoformat") else upd,
        "charge_power_kw": g("ev_charging_power"),
        "charge_remaining_min": g("ev_estimated_current_charge_duration"),
        "charge_limit_ac": g("ev_charge_limits_ac"),
        "charge_limit_dc": g("ev_charge_limits_dc"),
        "soh": g("ev_battery_soh_percentage"),
        "batt_temp_min": g("ev_battery_temperature_min"),
        "batt_temp_max": g("ev_battery_temperature_max"),
        "locked": _flag(g("is_locked")),
        "doors_open": _any_open(v, DOORS),
        "windows_open": _any_open(v, WINDOWS),
        # TODO verificar unidad (en un EV3 se vio 104). Se guarda en bruto.
        "consumption_30d": g("power_consumption_30d"),
        "total_consumed_wh": g("total_power_consumed"),
        "total_regen_wh": g("total_power_regenerated"),
    }


def daily_stats(v) -> list:
    """Consumo diario por categorías (Wh) del vehículo ya leído. No hace llamadas extra."""
    out = []
    for d in getattr(v, "daily_stats", None) or []:
        day = getattr(d, "date", None)
        if not hasattr(day, "strftime"):
            continue
        out.append({
            "day": day.strftime("%Y%m%d"),
            "total_wh": getattr(d, "total_consumed", None),
            "engine_wh": getattr(d, "engine_consumption", None),
            "climate_wh": getattr(d, "climate_consumption", None),
            "electronics_wh": getattr(d, "onboard_electronics_consumption", None),
            "battery_care_wh": getattr(d, "battery_care_consumption", None),
            "regen_wh": getattr(d, "regenerated_energy", None),
            "distance_km": getattr(d, "distance", None),
        })
    return out


def day_trips(yyyymmdd: str) -> list:
    """Viajes de un día (distancia, tiempos, velocidades). Kia UE no da la ruta dibujada."""
    vm = _manager()
    v = _vehicle(vm)
    try:
        vm.update_day_trip_info(v.id, yyyymmdd)
    except Exception as e:
        logging.warning("Sin info de viajes %s: %s", yyyymmdd, e)
        return []
    info = getattr(v, "day_trip_info", None)
    out = []
    for t in getattr(info, "trip_list", None) or []:
        out.append({
            "day": yyyymmdd,
            "start_time": str(getattr(t, "hhmmss", "")),
            "distance_km": getattr(t, "distance", None),
            "drive_min": getattr(t, "drive_time", None),
            "idle_min": getattr(t, "idle_time", None),
            "avg_speed": getattr(t, "avg_speed", None),
            "max_speed": getattr(t, "max_speed", None),
        })
    return out
