# Ajustes

Hay dos sitios para configurar MiEV3:

- **⚙ Ajustes en el panel:** lo que cambia con el uso (precios, consumo, umbrales…). Se guarda en
  la base de datos, se aplica sin reiniciar y tiene prioridad sobre el `.env`.
- **Fichero `.env`:** credenciales, contraseñas, tokens, puerto y región. Si cambias algo aquí,
  ejecuta `pm2 restart miev3-api miev3-collector --update-env`. También da el valor inicial de los ajustes del panel.

Si escribes un valor fuera de rango, el panel avisa y no guarda **ningún** cambio de ese envío.
En el `.env`, un número ilegible o fuera de rango no impide arrancar: se lleva al límite (o al
valor por defecto) y queda un aviso en el log. Se admite la coma decimal (`0,15`).

## En el panel

| Ajuste | Variable | Rango | Para qué sirve |
|---|---|---|---|
| Nombre del coche | `CAR_NAME` | 1–40 caracteres | Título del panel, de los informes y de los avisos |
| Color del coche | `CAR_COLOR` | lista | Tiñe el panel y los informes con el color de tu carrocería (ver abajo) |
| Batería útil | `BATTERY_KWH` | 10–200 kWh | Pasar el % de batería a kWh. Usa la capacidad **neta** de la ficha técnica |
| Consumo homologado (WLTP) | `WLTP_KWH_100` | 5–40 kWh/100 km | Marca de referencia en los relojes de eficiencia |
| Precio de la electricidad | `PRICE_EUR_KWH` | 0,001–2 €/kWh | Coste de las cargas nuevas y de los costes estimados. Pon el precio **final**, con impuestos |
| Eficiencia de carga | `CHARGE_EFFICIENCY` | 0,5–1 | Pérdidas de la carga. 0,90 = el 10 % se pierde por el camino |
| Pausa que une cargas | `CHARGE_MERGE_GAP_MIN` | 0–600 min | Pausas más cortas no parten la carga en varias |
| Combustible | `FUEL_TYPE` | `diesel` o `gasolina` | Coche de combustión con el que te comparas. Fija también la energía de 1 L para el «L/100 km equivalente»: gasóleo 10,0 kWh/L, gasolina 8,9 kWh/L |
| Precio del litro | `FUEL_PRICE_EUR_L` | 0,1–5 €/L | Comparación con combustión |
| Consumo de ese coche | `FUEL_L_100` | 2–20 L/100 km | Comparación con combustión |
| Aviso de batería baja | `LOW_SOC_ALERT` | 0–100 % | Umbral del aviso 🪫 |
| Aviso de coche sin datos | `KIA_DOWN_ALERT_MIN` | 30–10.080 min | Tiempo sin poder leer el coche antes de avisar |
| Repetir ese aviso cada | `KIA_DOWN_REPEAT_H` | 1–168 h | Repetición mientras siga sin datos |
| Leer el coche cada | `KIA_INTERVAL_MIN` | 30–1.440 min | No se permite menos de 30, para no agotar el cupo de la API |
| Latitud / longitud de casa | `HOME_LAT`, `HOME_LON` | vacío o coordenadas | Marca «🏠 Casa» en los avisos de carga |
| Radio de casa | `HOME_RADIUS_M` | 10–10.000 m | Distancia que todavía cuenta como casa |
| Pausar si la casa consume | `LOAD_HIGH_KW` | 0,1–30 kW | Control de carga con Home Assistant ([control-carga.md](control-carga.md)) |
| Reanudar si baja de / Tiempo por debajo para reanudar | `LOAD_LOW_KW`, `LOAD_LOW_MIN` | 0–30 kW / 1–120 min | Control de carga: cuándo se reanuda |
| Pausa máxima | `LOAD_PAUSE_MAX_MIN` | 10–600 min | Control de carga: tope de cada pausa |
| Potencia del cargador de casa | `LOAD_CHARGER_MAX_KW` | 1–22 kW | Control de carga: solo actúa en casa y hasta esta potencia (nunca en un cargador público) |
| Pausas como mucho por carga | `LOAD_MAX_PAUSES` | 1–20 | Control de carga: al llegar, avisa y deja de pausar |

Notas:

- El precio de la luz y la eficiencia afectan de forma distinta a lo ya guardado. Las **cargas
  guardadas** conservan su coste. Las **cifras estimadas** del panel (€/100 km, gasto del mes,
  ahorro frente a diésel) se recalculan con los valores nuevos.
- **Colores:** `neutro` (verde azulado, sin color de coche), `blanco`, `plata`, `gris`, `negro`,
  `azul`, `verde`, `pistacho`, `terracota` y `naranja`. Los nombres salen de catálogos del EV3 de
  varios mercados europeos (*sin verificar* la lista exacta de España, que cambia por año y
  acabado) y los tonos son **aproximados**: Kia no publica valores de color. El panel usa una versión más oscura
  o más clara de cada uno para que el texto se lea bien en modo claro y oscuro.
- Para sacar las coordenadas de casa, haz clic derecho sobre tu casa en Google Maps; el primer
  número es la latitud.

## Solo en `.env`

| Variable | Para qué sirve |
|---|---|
| `KIA_USER`, `KIA_PASSWORD`, `KIA_PIN` | Cuenta de la app del fabricante. En Europa puede hacer falta un refresh token como contraseña |
| `CAR_REGION` | 1 Europa, 2 Canadá, 3 EE. UU., 4 China, 5 Australia… (solo probado: 1) |
| `CAR_BRAND` | 1 Kia, 2 Hyundai, 3 Genesis (solo probado: 1) |
| `CAR_VIN` | Solo con varios coches en la cuenta: el VIN del que se lee. Vacío = el primero (el log lista los VIN) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Avisos por Telegram |
| `NOTIFY_URL`, `NOTIFY_SECRET`, `NOTIFY_DRY_RUN` | Avisos por webhook propio (si no hay Telegram) |
| `DASH_USER`, `DASH_PASS` | Acceso al panel. `DASH_PASS` necesita 8 caracteres o más, sin ñ ni tildes; si no, no entra nadie |
| `API_PORT` | Puerto local del panel (8090) |
| `INGEST_TOKEN` | Token que debe enviar el agente del V2C Trydan (16 caracteres o más; vacío = ingesta desactivada) |
| `HA_URL`, `HA_TOKEN`, `HA_POWER_ENTITY` | Home Assistant para el control de carga (opcional; vacío = apagado) |
| `DB_PATH` | Ruta de la base de datos (`miev3.db`) |
| `TZ` | Tu zona horaria: horarios (viajes 23:45, resumen domingo 20:00) y días locales |

## Volver a los valores del `.env`

Un ajuste guardado en el panel tiene prioridad sobre el `.env`. Para volver al valor del `.env`,
bórralo de la base de datos y reinicia:

```bash
sqlite3 miev3.db "DELETE FROM settings WHERE k='PRICE_EUR_KWH'"
pm2 restart miev3-api miev3-collector
```
