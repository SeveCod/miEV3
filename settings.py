"""Ajustes editables desde el panel (⚙ Ajustes).

Se guardan en la tabla `settings` de SQLite y se superponen a los valores de .env en el
módulo `config`, así el resto del código sigue leyendo `config.X`. La API los recarga al
guardar y el colector en cada vuelta (una por minuto), sin reiniciar nada.
Los secretos (credenciales, contraseñas, tokens) y el puerto NO están aquí: van en .env.
"""
import logging
from dataclasses import dataclass, asdict
import config, db


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    kind: str              # "text" | "float" | "int" | "coord"
    lo: float | None = None
    hi: float | None = None
    unit: str = ""
    help: str = ""


FIELDS = (
    Field("CAR_NAME", "Nombre del coche", "Coche", "text", 1, 40,
          help="Aparece en el título del panel y en los avisos."),
    Field("BATTERY_KWH", "Batería útil", "Coche", "float", 10, 200, "kWh",
          help="Capacidad neta de la ficha técnica. Con ella se pasa el % de batería a kWh."),
    Field("WLTP_KWH_100", "Consumo homologado (WLTP)", "Coche", "float", 5, 40, "kWh/100 km",
          help="Referencia que marcan los relojes de eficiencia."),
    Field("PRICE_EUR_KWH", "Precio de la electricidad", "Carga", "float", 0.001, 2, "€/kWh",
          help="Precio final con impuestos. Se usa para las cargas nuevas y los costes estimados."),
    Field("CHARGE_EFFICIENCY", "Eficiencia de carga", "Carga", "float", 0.5, 1, "0–1",
          help="Parte de la energía de la red que acaba en la batería (0,85–0,92 en AC)."),
    Field("CHARGE_MERGE_GAP_MIN", "Pausa que une cargas", "Carga", "float", 0, 600, "min",
          help="Tramos separados por menos de esto cuentan como una sola carga."),
    Field("DIESEL_PRICE_EUR_L", "Precio del diésel", "Comparación con diésel", "float", 0.1, 5, "€/L",
          help="Para calcular cuánto te ahorras frente a un coche diésel."),
    Field("DIESEL_L_100", "Consumo del diésel de referencia", "Comparación con diésel", "float", 2, 20, "L/100 km",
          help="Lo que gastaría un coche diésel equivalente (o el que tenías antes)."),
    Field("DIESEL_KWH_PER_L", "Energía de 1 L de diésel", "Comparación con diésel", "float", 5, 15, "kWh/L",
          help="Para el consumo equivalente en L/100 km. Unos 10 kWh/L."),
    Field("LOW_SOC_ALERT", "Aviso de batería baja", "Avisos", "float", 0, 100, "%",
          help="Avisa una vez si baja de aquí sin estar enchufado."),
    Field("KIA_DOWN_ALERT_MIN", "Aviso de coche sin datos", "Avisos", "float", 30, 10080, "min",
          help="Avisa si en este tiempo no se ha podido leer el coche."),
    Field("KIA_DOWN_REPEAT_H", "Repetir ese aviso cada", "Avisos", "float", 1, 168, "h"),
    Field("KIA_INTERVAL_MIN", "Leer el coche cada", "Avisos", "int", 30, 1440, "min",
          help="Mínimo 30: lecturas más seguidas agotan el cupo diario de la API."),
    Field("HOME_LAT", "Latitud de casa", "Casa", "coord", -90, 90,
          help="Opcional. Las cargas cerca de este punto se marcan como «Casa»."),
    Field("HOME_LON", "Longitud de casa", "Casa", "coord", -180, 180),
    Field("HOME_RADIUS_M", "Radio de casa", "Casa", "float", 10, 10000, "m"),
)
BY_KEY = {f.key: f for f in FIELDS}


def _parse(f: Field, raw):
    """Valor validado para config, o ValueError con un mensaje legible."""
    if f.kind == "text":
        v = str(raw).strip()
        if not f.lo <= len(v) <= f.hi:
            raise ValueError(f"{f.label}: entre {f.lo:.0f} y {f.hi:.0f} caracteres")
        return v
    if f.kind == "coord" and str(raw).strip() == "":
        return ""  # sin casa
    try:
        n = float(str(raw).replace(",", "."))
    except ValueError:
        raise ValueError(f"{f.label}: tiene que ser un número") from None
    if n != n or not f.lo <= n <= f.hi:
        raise ValueError(f"{f.label}: entre {f.lo:g} y {f.hi:g}")
    if f.kind == "int":
        return int(round(n))
    return str(n) if f.kind == "coord" else n


def load():
    """Superpone a config los ajustes guardados. Un valor ilegible se ignora con aviso en el log."""
    with db.conn() as c:
        saved = c.execute("SELECT k, v FROM settings").fetchall()
    for k, v in saved:
        f = BY_KEY.get(k)
        if f is None:
            continue
        try:
            setattr(config, k, _parse(f, v))
        except ValueError as e:
            logging.warning("Ajuste guardado no válido, se ignora: %s", e)


def save(data: dict):
    """Valida todo antes de escribir nada; si algo falla, ValueError y no se guarda ningún cambio."""
    unknown = set(data) - set(BY_KEY)
    if unknown:
        raise ValueError(f"Ajustes desconocidos: {', '.join(sorted(unknown))}")
    clean = {k: _parse(BY_KEY[k], v) for k, v in data.items()}
    with db.conn() as c:
        c.executemany("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      [(k, str(v)) for k, v in clean.items()])
    load()


def describe() -> list[dict]:
    """Definición y valor actual de cada ajuste, para pintar el formulario."""
    return [{**asdict(f), "value": getattr(config, f.key)} for f in FIELDS]
