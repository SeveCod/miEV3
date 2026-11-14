// TZ: horarios (viajes 23:45, resumen domingo 20:00) y días locales; cámbiala por la tuya. Las marcas se guardan en UTC
const env = { TZ: "Europe/Madrid" };
module.exports = {
  apps: [
    { name: "miev3-collector", script: "main.py", interpreter: "./venv/bin/python", cwd: __dirname, env },
    { name: "miev3-api", script: "api.py", interpreter: "./venv/bin/python", cwd: __dirname, env }
  ]
};
