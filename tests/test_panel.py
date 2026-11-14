"""Guardia estática del panel: toda función render*/helper llamada en index.html está definida."""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()


def test_render_llamadas_definidas():
    called = set(re.findall(r"\b(render\w+|speedColor|gaugeSVG)\(", HTML))
    defined = set(re.findall(r"function (\w+)\(", HTML))
    assert called and called <= defined, f"sin definir: {called - defined}"
