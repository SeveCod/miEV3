# Control de carga por consumo de la casa (opcional, experimental)

Si tu potencia contratada es justa, coche y casa a la vez pueden hacer saltar el limitador (ICP).
Ejemplo real: coche a 5,7 kW + termo de 1,7 kW con 6 kW contratados → saltó tres veces en una tarde.

MiEV3 puede **pausar la carga** cuando la casa consume mucho y **reanudarla** cuando baja. Lee
el consumo de la casa de **Home Assistant** y manda la orden de parar o reanudar a la nube del coche.

> **Experimental.** Parar y reanudar la carga solo se ha probado en un Kia EV3 de Europa. Las
> órdenes despiertan el coche: MiEV3 solo las envía cuando hace falta, nunca en bucle.

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

Sin estas tres variables el control está apagado. Los umbrales se cambian en ⚙ Ajustes:

| Ajuste | Variable | Por defecto | Qué hace |
|---|---|---|---|
| Pausar si la casa consume | `LOAD_HIGH_KW` | 1,0 kW | A partir de aquí se pausa la carga |
| Reanudar si baja de | `LOAD_LOW_KW` | 0,6 kW | Umbral para reanudar |
| Tiempo por debajo para reanudar | `LOAD_LOW_MIN` | 10 min | Tras este tiempo seguido por debajo, se reanuda |
| Pausa máxima | `LOAD_PAUSE_MAX_MIN` | 90 min | Pasado este tiempo se reanuda aunque la casa siga alta |
| Pausas como mucho por carga | `LOAD_MAX_PAUSES` | 4 | Al llegar, avisa y deja de pausar en esa carga |

Elige `LOAD_HIGH_KW` como *potencia contratada − potencia de carga del coche*, con algo de margen.

## Reglas de seguridad

- Solo pausa si el coche está **en casa** (si has puesto las coordenadas), **enchufado** y
  **cargando**, y no ha llegado ya a su límite de carga. Un coche que no carga no se toca.
- Solo reanuda una carga que pausó él mismo.
- Si Home Assistant no responde, no decide nada; una pausa nunca dura más que la pausa máxima.
- Si una orden falla tres veces, avisa para que lo revises en la app del coche.
- El aviso de «Carga terminada» dice cuántas pausas hubo y si la batería se quedó por debajo
  del límite de carga del coche.

## Limitaciones

- El sensor de la casa puede actualizarse cada pocos minutos: un pico muy rápido puede hacer
  saltar el limitador antes de que llegue la pausa.
- El estado del coche es el de la caché de la nube (no se fuerza el refresco).
