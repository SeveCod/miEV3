"""Ajustes editables desde el panel (⚙ Ajustes).

Se guardan en la tabla `settings` de SQLite y se superponen a los valores de .env en el
módulo `config`, así el resto del código sigue leyendo `config.X`. La API los recarga al
guardar y el colector en cada vuelta (una por minuto), sin reiniciar nada.
Los secretos (credenciales, contraseñas, tokens) y el puerto NO están aquí: van en .env.
"""
import logging
from dataclasses import dataclass, asdict, field
import config, db

# Combustible del coche con el que te comparas: etiqueta y energía de 1 L (kWh, poder calorífico
# inferior) para el «L/100 km equivalente».
FUELS = {
    "diesel": {"label": "Gasóleo (diésel)", "short": "diésel", "kwh_per_l": 10.0},
    "gasolina": {"label": "Gasolina", "short": "gasolina", "kwh_per_l": 8.9},
}

# Colores de carrocería del Kia EV3 (nombres de catálogo europeo; varían por mercado y año).
# paint = tono aproximado de la pintura, para la muestra; accent / accent_dark = versión legible
# para la interfaz en modo claro / oscuro (texto blanco sobre accent con contraste ≥ 4,5:1).
# Los hex son aproximados: no hay valores oficiales publicados.
CAR_COLORS = {
    "neutro":    {"label": "Sin color de coche", "paint": "#2F8F83", "accent": "#2B7F75", "accent_dark": "#3BA597"},
    "blanco":    {"label": "Blanco perla (Snow White Pearl)", "paint": "#ECECE7", "accent": "#4F5A63", "accent_dark": "#C9CFD3"},
    "plata":     {"label": "Plata (Ivory / Lithium Silver)", "paint": "#B4B8BA", "accent": "#5C6466", "accent_dark": "#B9C0C2"},
    "gris":      {"label": "Gris pizarra (Shale Grey)", "paint": "#6D716F", "accent": "#555A58", "accent_dark": "#A8AEAB"},
    "negro":     {"label": "Negro (Aurora Black Pearl / Midnight Black)", "paint": "#1F2226", "accent": "#30363C", "accent_dark": "#AEB6BD"},
    "azul":      {"label": "Azul hielo (Frost Blue)", "paint": "#A8BFCB", "accent": "#3D6A82", "accent_dark": "#8FB3C6"},
    "verde":     {"label": "Verde aventurina (Aventurine Green)", "paint": "#3D5A4C", "accent": "#3B6552", "accent_dark": "#7FB39A"},
    "pistacho":  {"label": "Verde pistacho (Pistachio Green)", "paint": "#BCC9A4", "accent": "#56703A", "accent_dark": "#A9C08A"},
    "terracota": {"label": "Terracota (Terracotta)", "paint": "#B4634A", "accent": "#9E5039", "accent_dark": "#D98C72"},
    "naranja":   {"label": "Naranja (Sunset Orange)", "paint": "#D9662E", "accent": "#A84D1C", "accent_dark": "#E8915F"},
}


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    kind: str              # "text" | "float" | "int" | "coord" | "choice"
    lo: float | None = None
    hi: float | None = None
    unit: str = ""
    help: str = ""
    options: dict = field(default_factory=dict)   # solo "choice": clave → {"label", …}


FIELDS = (
    Field("CAR_NAME", "Nombre del coche", "Coche", "text", 1, 40,
          help="Aparece en el título del panel y en los avisos."),
    Field("CAR_COLOR", "Color del coche", "Coche", "choice", options=CAR_COLORS,
          help="El panel y los informes toman el color de tu carrocería."),
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
    Field("FUEL_TYPE", "Combustible", "Comparación con combustión", "choice", options=FUELS,
          help="El del coche con el que te comparas (el que tenías antes o uno equivalente)."),
    Field("FUEL_PRICE_EUR_L", "Precio del litro", "Comparación con combustión", "float", 0.1, 5, "€/L",
          help="Para calcular cuánto te ahorras frente a ese coche."),
    Field("FUEL_L_100", "Consumo de ese coche", "Comparación con combustión", "float", 2, 20, "L/100 km",
          help="Lo que gastaría el coche de combustión en los mismos km."),
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
    if f.kind == "choice":
        v = str(raw).strip()
        if v not in f.options:
            raise ValueError(f"{f.label}: opción no válida")
        return v
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


def _check_env():
    """Los valores de .env pasan la misma validación que los del panel. Uno fuera de rango se
    lleva al límite (KIA_INTERVAL_MIN=5 → 30) y una opción desconocida a la primera de la lista,
    con aviso en el log, para que un error de tecleo no tumbe el panel ni agote la API."""
    for f in FIELDS:
        cur = getattr(config, f.key)
        try:
            setattr(config, f.key, _parse(f, cur))
        except ValueError as e:
            if f.kind == "choice":
                fixed = next(iter(f.options))
            elif f.kind in ("float", "int"):
                fixed = _parse(f, min(max(float(cur), f.lo), f.hi))
            elif f.kind == "coord":
                fixed = ""                                   # sin casa
            else:
                fixed = str(cur).strip()[:int(f.hi)] or "Mi EV3"
            logging.warning("Valor de .env no válido (%s); se usa %r", e, fixed)
            setattr(config, f.key, fixed)


def load():
    """Valida los valores de .env y superpone los ajustes guardados en el panel. Un ajuste
    guardado ilegible se ignora con aviso en el log."""
    _check_env()
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
