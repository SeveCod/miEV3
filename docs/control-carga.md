# Control de carga por consumo de la casa (opcional, experimental)

Si tu potencia contratada es justa, coche y casa a la vez pueden hacer saltar el limitador (ICP).
Ejemplo real: coche a 5,7 kW + termo de 1,7 kW con 6 kW contratados → saltó tres veces en una tarde.

MiEV3 puede **pausar la carga** cuando la casa consume mucho y **reanudarla** cuando baja. Lee
el consumo de la casa de **Home Assistant** y manda la orden de parar o reanudar a la nube del coche.

> **Experimental.** Parar y reanudar la carga solo se ha probado en un Kia EV3 de Europa.

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
| Pausa máxima | `LOAD_PAUSE_MAX_MIN` | 90 min | Pasado este tiempo se reanuda aunque la casa siga alta |
| Potencia del cargador de casa | `LOAD_CHARGER_MAX_KW` | 7,4 kW | Una carga más potente no es la de casa y no se toca |
| Pausas como mucho por carga | `LOAD_MAX_PAUSES` | 4 | Al llegar, avisa y deja de pausar en esa carga |

Elige `LOAD_HIGH_KW` como *potencia contratada − potencia de carga del coche*, con algo de margen.

## Cuándo actúa (y cuándo no pregunta nada)

- **Solo en el cargador de casa.** La API del coche no da un identificador del cargador, así que
  «el de casa» es: coche dentro del radio de casa y potencia de carga no mayor que
  `LOAD_CHARGER_MAX_KW`. En un cargador público (AC trifásico o rápido DC) nunca actúa.
- **Mínimas llamadas.** Al coche no se le pide ninguna lectura extra: se decide con la última que
  guarda el colector cada `KIA_INTERVAL_MIN`. A Home Assistant solo se le pregunta si esa lectura
  dice que el coche carga en casa por debajo de su límite, o durante una pausa. Con la batería en
  su límite, sin cargar o fuera de casa, no hay llamadas ni avisos.
- Solo reanuda una carga que pausó él. Una pausa nunca dura más que la pausa máxima, aunque Home
  Assistant no responda. Una pausa del control no parte la carga en dos.
- Si una orden falla tres veces seguidas, avisa y no insiste en esa carga.
- El aviso de «Carga terminada» dice cuántas pausas hubo y si la batería se quedó por debajo
  del límite de carga del coche.

## Limitaciones

- El sensor de la casa puede actualizarse cada pocos minutos: un pico muy rápido puede hacer
  saltar el limitador antes de que llegue la pausa.
- El estado del coche es el de la última lectura (cada 30 min por defecto): una carga que
  empieza justo después de una lectura no se vigila hasta la siguiente.
