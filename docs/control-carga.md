# Control de carga por consumo de la casa (opcional)

Si tu potencia contratada es justa, coche y casa a la vez pueden hacer saltar el limitador (ICP).
Ejemplo real: coche a 5,7 kW + termo de 1,7 kW con 6 kW contratados → saltó tres veces en una tarde.

MiEV3 puede **pausar la carga** cuando la casa consume mucho y **reanudarla** cuando baja. Lee
el consumo de la casa de **Home Assistant** y manda la orden de parar o reanudar a la nube del coche.

> **Probado en un Kia EV3 de Europa:** el coche obedece las órdenes de parar y reanudar la carga.
> En otros modelos o regiones no se ha comprobado.

## Qué necesitas

- Home Assistant accesible desde el servidor de MiEV3 (en la misma red, por VPN o Tailscale).
- Un sensor de **potencia de la casa SIN el cargador**, en kW (p. ej. un medidor en el cuadro
  que no cuente la línea del coche). Si el sensor incluye el coche, la carga se pausaría sola.
- Un token de acceso de larga duración de Home Assistant (Perfil → Seguridad).

## Configuración (`.env`)

```
HA_URL=http://IP_DE_HOME_ASSISTANT:8123
HA_TOKEN=token_de_larga_duracion
HA_POWER_ENTITY=sensor.potencia_casa     # tu entidad, en kW
```

Sin estas tres variables, o sin las coordenadas de casa (`HOME_LAT`/`HOME_LON`), el control está apagado. Los umbrales se cambian en ⚙ Ajustes:

| Ajuste | Variable | Por defecto | Qué hace |
|---|---|---|---|
| Pausar si la casa consume | `LOAD_HIGH_KW` | 1,0 kW | A partir de aquí se pausa la carga |
| Reanudar si baja de | `LOAD_LOW_KW` | 0,6 kW | Umbral para reanudar |
| Tiempo por debajo para reanudar | `LOAD_LOW_MIN` | 10 min | Tras este tiempo seguido por debajo, se reanuda |
| Pausa máxima | `LOAD_PAUSE_MAX_MIN` | 90 min | Pasado este tiempo se reanuda, salvo que la casa siga alta: entonces se alarga y avisa |
| Potencia del cargador de casa | `LOAD_CHARGER_MAX_KW` | 7,4 kW | Una carga más potente no es la de casa y no se toca |
| Pausas como mucho por carga | `LOAD_MAX_PAUSES` | 8 | Al llegar, avisa y deja de pausar en esa carga |

Elige `LOAD_HIGH_KW` como *potencia contratada − potencia de carga del coche*, con algo de margen.

## Cuándo actúa (y cuándo no pregunta nada)

- **Solo en el cargador de casa.** La API del coche no da un identificador del cargador, así que
  «el de casa» es: coche dentro del radio de casa y potencia de carga no mayor que
  `LOAD_CHARGER_MAX_KW`. En un cargador público (AC trifásico o rápido DC) nunca actúa.
- **Mínimas llamadas.** Se decide con la última lectura que guarda el colector cada
  `KIA_INTERVAL_MIN`. A Home Assistant solo se le pregunta con el coche en casa por debajo de su
  límite (cargando, en pausa o con una lectura de más de 10 min). Con la batería en su límite o
  fuera de casa, no hay llamadas ni avisos.
- **Arranque vigilado:** si la casa está alta, el coche en casa por debajo de su límite y la
  última lectura tiene más de 10 min, se lee el coche en caché (no lo despierta) y se pausa si ya
  carga. Como mucho una lectura cada 15 min y 12 al día.
- Solo reanuda una carga que pausó él. A la pausa máxima reanuda si la casa ya bajó o Home Assistant
  no responde; si la casa sigue alta, alarga la pausa y avisa una vez. Una pausa del control no parte la carga en dos.
- Si una orden falla tres veces seguidas, avisa y no insiste en esa carga.
- Pausar y reanudar por la casa no manda aviso (llegaría tarde, en el resumen del día). El aviso de
  «Carga terminada» dice cuántas pausas hubo y si la batería se quedó por debajo del límite de
  carga del coche. Sí avisan: el tope de pausas, una orden que falla y la reanudación a la pausa máxima.

## Limitaciones

- El sensor de la casa puede actualizarse cada pocos minutos: un pico muy rápido puede hacer
  saltar el limitador antes de que llegue la pausa.
- Con el tope de 12 lecturas extra al día agotado, una carga que empieza justo después de una
  lectura no se vigila hasta la siguiente (cada 30 min por defecto).
