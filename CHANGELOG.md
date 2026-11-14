# Cambios

## 0.2.0-beta (2026-11-14)

Endurecimiento tras la auditoría de la primera beta.

- **Seguridad:**
  - Las contraseñas y tokens de ejemplo ya no dan acceso. `DASH_PASS` necesita 8 caracteres o más, sin ñ ni tildes.
  - CSP y cabeceras de seguridad; el JavaScript sale a `/app.js` y Chart.js va con SRI.
  - Fuera `/docs`; las imágenes van con contraseña; la BD se crea con permisos 0600.
- **Robustez:**
  - La ingesta del Trydan valida lo que recibe.
  - Los datos incompletos del coche ya no tumban el panel, que además pinta cada sección por separado.
  - Un `.env` mal escrito no impide arrancar.
  - Una carga ya no se guarda dos veces.
- **Base de datos:** esquema versionado (`PRAGMA user_version`) con migraciones numeradas y arranque simultáneo seguro.
- **Accesibilidad:** contraste ≥ 4,5:1 en los textos de color, foco visible, textos alternativos en gráficas y mapa, y bienvenida para usuarios nuevos.
- **Instalación:** `TZ` se lee del `.env`; dependencias con versión máxima; licencia MIT; tests en GitHub Actions.

## 0.1.0-beta (2026-11-14)

Primera beta pública: panel con el color del coche, comparación con gasóleo o gasolina, informes en PDF, guía de conexión e instalador guiado.
