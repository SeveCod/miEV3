# Cómo funciona

EV Brain no tiene acceso directo al coche. Cada 30 minutos lee el **último estado que el coche
subió a la nube del fabricante**, a través de la librería
[hyundai_kia_connect_api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api). Ese
estado puede tener minutos u horas: el panel enseña la hora del dato («Dato del coche: …»).
Todo lo demás se deduce de esas lecturas.

```
nube del fabricante ──(cada 30 min)──▶ colector (main.py) ──▶ evbrain.db (SQLite)
                                         │                         │
                                         └─▶ avisos (Telegram)      └─▶ API + panel (api.py)
cargador V2C (opcional) ──▶ agente en casa ──▶ POST /ingest/v2c ────┘
```

Hay dos procesos: el **colector** (`main.py`) y la **API con el panel** (`api.py`). Comparten la
base de datos y los ajustes. Un cambio en ⚙ Ajustes llega a la API al instante y al colector en
menos de un minuto.

## Cargas

Una carga se detecta cuando el coche pasa de «no cargando» a «cargando», y termina cuando deja de
cargar. Si el cargador hace pausas (perfiles horarios, potencia limitada) y la carga se reanuda
antes de **Pausa que une cargas** (90 min), todo cuenta como **una sola carga**. Así la carga se
cierra y se avisa solo cuando han pasado esos 90 minutos sin reanudarse.

Lo que cuesta cada carga se estima así:

```
kWh de la red = (batería final − batería inicial) / 100 × Batería útil ÷ Eficiencia de carga
coste          = kWh de la red × Precio de la electricidad
```

Ejemplo: de 40 % a 80 % con 58,3 kWh y una eficiencia de 0,90 → 0,40 × 58,3 ÷ 0,90 = 25,9 kWh.
A 0,15 €/kWh, la carga sale por **3,89 €**.

- El coste **se guarda al cerrar la carga**, con el precio de ese momento. Si cambias el precio
  después, las cargas antiguas mantienen el suyo.
- Todas las cargas se calculan con tu precio de casa, **también las de cargadores públicos o
  rápidos**, que suelen ser bastante más caros. Tenlo en cuenta si cargas mucho fuera.
- La **eficiencia** es la parte de la energía que sale del enchufe y acaba en la batería. En
  carga AC doméstica suele estar entre 0,85 y 0,92, y es peor a poca potencia. Si tienes un
  contador o el V2C Trydan, compara sus kWh con los estimados y ajústala.
- Si defines tu casa (latitud, longitud y radio), el aviso dice «🏠 Casa»; si no, da las
  coordenadas.

## Consumo real

El coche publica su propio contador de energía consumida (por día y acumulado). En el Kia EV3 ese
contador **se queda corto**, aproximadamente un 23 %, frente a lo que baja de verdad el % de
batería. Por eso EV Brain lo **calibra** así:

1. Busca tramos **sin cargar** en los que la batería baja al menos un 10 % y el coche recorre al
   menos 30 km.
2. En cada tramo compara la energía que salió de la batería (Δ% × Batería útil) con la que dice el
   contador.
3. Con al menos **300 km y 2 tramos**, calcula el factor global: energía de batería ÷ contador.
   Hasta entonces el factor es 1 y el panel avisa de que el dato es provisional.

Todas las cifras de consumo del panel (relojes, histórico, récords y costes estimados) son el
contador del coche × ese factor. Es un factor global, así que el consumo **de un solo día** es
aproximado.

El panel muestra estas cifras a partir del consumo:

| Cifra | Fórmula |
|---|---|
| kWh/100 km | energía (calibrada) ÷ km × 100 |
| €/100 km | kWh/100 ÷ Eficiencia de carga × Precio de la electricidad |
| L/100 km equivalente | kWh/100 ÷ Energía de 1 L de diésel (≈ 10 kWh/L). Es una equivalencia de **energía**, no de dinero |
| Autonomía al 100 % | Batería útil ÷ kWh/100 × 100 |
| Relojes de eficiencia | la marca es tu consumo homologado (WLTP); en verde si estás por debajo y en ámbar si estás por encima |

## Comparación con diésel

Defines un coche diésel de referencia, normalmente el que tenías antes o uno equivalente:

```
diésel €/100 km = Consumo del diésel de referencia × Precio del diésel
ahorro          = (diésel €/100 km − tu €/100 km) × km recorridos ÷ 100
```

Ejemplo: 5,5 L/100 km × 1,45 €/L = 7,98 €/100 km. Si tu coche sale a 2,50 €/100 km, en 1.000 km
te ahorras (7,98 − 2,50) × 10 = **54,8 €**.

El resumen muestra el ahorro de los últimos 30 días y desde que el coche es nuevo (con el
odómetro y el consumo acumulado del coche). Se calcula con los precios **actuales**: si cambias el
precio del diésel o el de la luz, el ahorro histórico se recalcula entero. **No incluye** cambios
de aceite, impuestos, seguro ni la diferencia de precio de compra.

## Viajes

A las 23:45 el colector descarga los viajes del día: hora de salida, km, minutos en marcha y
parado, y velocidad media y máxima. Si falla, lo intenta dos veces más con 5 minutos de
diferencia. La nube europea **no da la ruta**, así que no hay mapa de recorridos.

Con los viajes se calculan los recorridos por día, los récords, los hábitos («cuándo conduces») y
la proyección de km al año.

## Avisos

| Aviso | Cuándo |
|---|---|
| ✅ Carga terminada | Al cerrar cada carga: % inicial y final, kWh, €, lugar y horas |
| 🪫 Batería baja | Batería ≤ umbral y sin enchufar. Avisa **una vez**, y no vuelve a avisar hasta que la batería supera el umbral + 5 |
| ⚠️ Sin datos del coche | Si no se puede leer en *Aviso de coche sin datos* (2 h); se repite cada 12 h mientras siga así, y avisa cuando vuelve |
| 📊 Resumen semanal | Domingos a las 20:00: km, cargas, kWh y € de la semana |

## Lo que no está verificado

- **Energía recuperada (frenada regenerativa):** el panel la muestra, pero sus cifras no cuadran
  con el balance de batería. Está marcada «pendiente de aclarar».
- **Estado «cargando» en coches CCS2** (como el EV3): la librería lo deduce y a veces no lo sabe.
  En ese caso se mantiene el estado anterior para no cerrar una carga en falso.
- **Otros modelos y regiones:** solo se ha probado con Kia en Europa.
- **Agente del V2C Trydan:** el endpoint y los campos dependen del firmware.
- **Cargas que cruzan la medianoche:** en el recorrido diario se reparten entre los dos días; en el
  histórico cuentan en el día en que terminan.
