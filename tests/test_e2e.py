"""Panel de punta a punta en un navegador real (Playwright): demo sembrada, sin errores de consola,
sin scroll horizontal y con las secciones pintadas. Si Playwright no está instalado, se salta:
    venv/bin/pip install playwright && venv/bin/python -m playwright install chromium
"""
import base64, os, socket, subprocess, sys, time, urllib.request
from pathlib import Path
import pytest

sync_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
PASS = "e2e-password"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    db = tmp_path_factory.mktemp("e2e") / "demo.db"
    port = _free_port()
    env = {**os.environ, "DB_PATH": str(db), "DASH_PASS": PASS, "API_PORT": str(port), "TZ": "Europe/Madrid"}
    subprocess.run([sys.executable, "scripts/seed_demo.py"], cwd=ROOT, env=env, check=True, capture_output=True)
    proc = subprocess.Popen([sys.executable, "api.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    req = urllib.request.Request(url, headers={"Authorization": "Basic " + base64.b64encode(f"admin:{PASS}".encode()).decode()})
    for _ in range(100):
        try:
            urllib.request.urlopen(req, timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("la API de la demo no arrancó")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.mark.parametrize("width,scheme", [(390, "light"), (1440, "dark")])
def test_panel_e_informe(server, browser, width, scheme):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme,
                              http_credentials={"username": "admin", "password": PASS})
    page, errors = ctx.new_page(), []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(server, wait_until="networkidle")

    assert page.text_content("#soc").strip().endswith("%")
    assert page.locator("#summary .stat").count() == 6
    assert page.locator("#days .day").count() > 0
    assert "no se ha podido cargar" not in page.text_content("#updated")
    assert page.evaluate("document.documentElement.scrollWidth - innerWidth") == 0

    page.click("#openset")
    assert page.locator("#setfields .colors input").count() == 10
    page.click("#setcancel")

    page.click("#openrep")
    page.locator("#replines tr").first.wait_for()   # (wait_for_function usa eval: la CSP lo bloquea)
    assert "Informe mensual" in page.text_content("#reptitle")
    assert not page.is_disabled("#repprint")
    assert page.evaluate("document.documentElement.scrollWidth - innerWidth") == 0
    assert errors == []
    ctx.close()
