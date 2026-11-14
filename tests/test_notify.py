"""Avisos: Telegram directo si hay token y chat; si no, webhook; si no, solo log.
Siempre con parse_mode HTML y el texto escapado (un webhook que reenvía `parse_mode: null`
hace que Telegram lo rechace con «unsupported parse_mode»)."""
import pytest


class _Resp:
    status_code = 200
    text = "{}"


@pytest.fixture
def enviados(monkeypatch):
    import config, notify
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NOTIFY_URL"):
        monkeypatch.setattr(config, k, "")
    monkeypatch.setattr(config, "NOTIFY_SECRET", "x")
    lista = []
    monkeypatch.setattr(notify.requests, "post", lambda url, **kw: lista.append((url, kw["json"])) or _Resp())
    return config, notify, lista


def test_sin_canal_no_envia(enviados):
    config, notify, lista = enviados
    assert notify.notify("hola") is False and lista == []


def test_telegram_directo(enviados, monkeypatch):
    config, notify, lista = enviados
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(config, "NOTIFY_URL", "http://webhook")  # Telegram tiene prioridad
    assert notify.notify("✅ Carga terminada · 61% → 90%")
    url, body = lista[0]
    assert url == "https://api.telegram.org/bot123:abc/sendMessage"
    assert body["chat_id"] == "42" and body["parse_mode"] == "HTML"


def test_webhook(enviados, monkeypatch):
    config, notify, lista = enviados
    monkeypatch.setattr(config, "NOTIFY_URL", "http://webhook")
    assert notify.notify("✅ Carga terminada", "INFO", event_id="e1")
    url, body = lista[0]
    assert url == "http://webhook" and body["parse_mode"] == "HTML"
    assert body["urgency"] == "INFO" and body["event_id"] == "e1"


def test_escapa_el_texto(enviados, monkeypatch):
    config, notify, lista = enviados
    monkeypatch.setattr(config, "NOTIFY_URL", "http://webhook")
    notify.notify("⚠️ Sin datos · <html> & 'error' 400")
    assert lista[0][1]["text"] == "⚠️ Sin datos · &lt;html&gt; &amp; 'error' 400"
