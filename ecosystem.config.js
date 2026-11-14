// pm2: colector y panel. La zona horaria (TZ) y todo lo demás se configuran en .env
module.exports = {
  apps: [
    { name: "miev3-collector", script: "main.py", interpreter: "./venv/bin/python", cwd: __dirname },
    { name: "miev3-api", script: "api.py", interpreter: "./venv/bin/python", cwd: __dirname }
  ]
};
