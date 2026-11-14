"""Avisos: Telegram directo (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID) o, si no, un webhook
(NOTIFY_URL, POST JSON). Sin ninguno de los dos, el aviso solo queda en el log.
Best-effort: un aviso caído se registra y nunca tumba al colector."""
import html, logging, requests, config


def has_channel() -> bool:
    """Hay Telegram o webhook configurado (sin ninguno, los avisos solo van al log)."""
    return bool((config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID) or config.NOTIFY_URL)


def notify(text: str, urgency: str = "TODAY", event_id: str | None = None) -> bool:
    try:
        if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
            r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                              timeout=15, json={"chat_id": config.TELEGRAM_CHAT_ID, "parse_mode": "HTML",
                                                "text": html.escape(text, quote=False)})
        elif config.NOTIFY_URL:
            # urgency (TODAY/INFO) y event_id (para deduplicar) los interpreta el receptor
            r = requests.post(config.NOTIFY_URL, timeout=15,
                              headers={"X-Notify-Secret": config.NOTIFY_SECRET},
                              json={"topic": "coche", "urgency": urgency, "parse_mode": "HTML",
                                    "text": html.escape(text, quote=False),
                                    "event_id": event_id, "source": "miev3",
                                    "dry_run": config.NOTIFY_DRY_RUN})
        else:
            logging.info("Aviso sin canal configurado (no enviado): %s", text)
            return False
        if r.status_code == 200:
            return True
        logging.warning("Aviso no entregado (%s): %s", r.status_code, r.text[:200])
    except Exception as e:
        # Sin el texto de la excepción en bruto: en Telegram la URL lleva el token
        logging.warning("Fallo al enviar aviso: %s", type(e).__name__)
    return False
