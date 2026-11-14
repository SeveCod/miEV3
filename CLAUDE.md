# CLAUDE.md — MiEV3

App en beta para dueños de un Kia EV3 (y otros eléctricos Kia, Hyundai o Genesis con app
conectada). Lee la nube del fabricante, estima el coste de las cargas, guarda viajes, compara con
gasóleo o gasolina, saca informes en PDF, avisa por Telegram o webhook y lo muestra en un panel
web con el color del coche. Uso: `README.md`. Conexión y API: `docs/conectar-api.md`. Cálculos:
`docs/como-funciona.md`. Ajustes: `docs/ajustes.md`.

## Arquitectura

```
main.py            colector: coche cada KIA_INTERVAL_MIN (≥ 30), viajes a las 23:45, resumen el domingo a las 20:00
api.py             FastAPI en 127.0.0.1:$API_PORT; panel y /api/* con HTTP Basic; /ingest/v2c con X-Token
                   /api/history y /api/report comparten _aggregate()
settings.py        ajustes editables (tabla settings) superpuestos a config; load() en cada vuelta del colector;
                   FUELS (combustibles) y CAR_COLORS (paleta del tema)
config.py          valores de .env (secretos y valores iniciales)
sessions.py        cargas por transición de «cargando», unión de pausas, aviso de batería baja
collectors/kia.py  hyundai_kia_connect_api, solo estado en caché
notify.py          Telegram directo → webhook → solo log
db.py              esquema SQLite y migraciones idempotentes
static/index.html  panel e informes (HTML + JS sin compilación, Chart.js por CDN); PDF = imprimir del navegador
scripts/seed_demo.py  base de datos de demostración
```

## Reglas

- **API del coche:** solo `update_all_vehicles_with_cached_state()`. **Nunca** forzar el refresco
  (gasta la 12 V y el cupo diario); intervalo mínimo de 30 min, y el ajuste lo hace cumplir.
- **Ajustes:** un parámetro de negocio nuevo va en `config.py` (valor de .env) **y** en
  `settings.FIELDS` con su rango. Los secretos solo en `.env`, nunca en `FIELDS`. El código lee
  siempre `config.X` en tiempo de llamada (no `from config import X`), para que la recarga funcione.
- **Panel:** cualquier texto que venga de ajustes o datos se pinta con `textContent`, no con
  `innerHTML`.
- **Tema:** los colores salen de `settings.CAR_COLORS` (`paint`, `accent`, `accent_dark`); un test
  exige contraste ≥ 4,5:1. En el CSS, nada de colores fijos de acento: usar `--charge`, `--fill`
  y `--paint`.
- **Sin marca:** nada de logos ni fotos del fabricante. La foto opcional del usuario va en
  `static/img/car.webp`, que no se versiona.
- **Robustez:** los fallos del coche o del aviso se registran y el colector sigue. Usar `getattr`
  defensivo, porque los atributos de la librería cambian entre versiones.
- **Login UE roto:** antes de tocar credenciales, mirar las releases e issues de la librería y
  subir la versión fijada en `requirements.txt`.
- **Marcas de tiempo:** en UTC con `+00:00` (`db.utcnow()`). `trips.day` y `daily_stats.day` son
  días locales del coche.
- **Dependencias mínimas:** SQL plano y nada de ORMs ni frameworks de frontend.
- Lo que dependa de algo no verificado se marca `# TODO verificar` y se cuenta en la respuesta.

## Comandos

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt pytest httpx
venv/bin/pytest                                        # sin red: coche simulado y BD temporal
DB_PATH=demo.db venv/bin/python scripts/seed_demo.py   # datos de demo
DB_PATH=demo.db DASH_PASS=demo venv/bin/python api.py  # panel con la demo
```

Verificar la UI con la demo, a 390, 768 y 1440 px: sin scroll horizontal, sin textos cortados y
sin errores de consola. La cabecera `Authorization` va solo hacia 127.0.0.1; en global rompe las
fuentes de Google por CORS.

## Sin verificar

- `regenerated_energy` de `daily_stats`: no cuadra con el balance de batería.
- Unidad de `power_consumption_30d`.
- Modelos y regiones distintos de Kia UE (probado con EV3).
- Endpoint `/RealTimeData` del Trydan y sus campos.
