import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from flask import Flask, current_app, jsonify, request

from .zernio_client import ZernioClient

logger = logging.getLogger(__name__)


def create_app(test_config: Optional[Dict[str, Any]] = None) -> Flask:
    load_dotenv()
    configure_logging()

    app = Flask(__name__)
    app.config.from_mapping(
        NOTIFIER_TOKEN="",
        RECIPIENT_PHONE="",
        ZERNIO_API_KEY="",
        ZERNIO_API_URL="https://zernio.com/api/v1",
        ZERNIO_ACCOUNT_ID="",
        ZERNIO_SENDER_PHONE="",
        ZERNIO_CONVERSATION_ID="",
        ZERNIO_TIMEOUT_SECONDS="70",
        ZERNIO_START_TEMPLATE_NAME="start_new_day_conversation",
        ZERNIO_START_TEMPLATE_LANGUAGE="en_US",
    )

    for key in (
        "NOTIFIER_TOKEN",
        "RECIPIENT_PHONE",
        "ZERNIO_API_KEY",
        "ZERNIO_API_URL",
        "ZERNIO_ACCOUNT_ID",
        "ZERNIO_SENDER_PHONE",
        "ZERNIO_CONVERSATION_ID",
        "ZERNIO_TIMEOUT_SECONDS",
        "ZERNIO_START_TEMPLATE_NAME",
        "ZERNIO_START_TEMPLATE_LANGUAGE",
    ):
        if key in os.environ:
            app.config[key] = os.environ[key]

    if test_config:
        app.config.update(test_config)

    if app.config.get("ZERNIO_CLIENT") is None:
        app.config["ZERNIO_CLIENT"] = ZernioClient.from_config(app.config)

    @app.get("/")
    def index():
        return jsonify(
            {
                "ok": True,
                "service": "whatsapp-notifier",
                "endpoints": ["/health", "/alert", "/start-new-day-conversation"],
            }
        )

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.post("/alert")
    def alert():
        started = time.monotonic()
        request_context = {
            "remote_addr": request.headers.get("X-Forwarded-For", request.remote_addr),
            "content_length": request.content_length,
            "content_type": request.content_type,
        }
        log_event(logger, logging.INFO, "alert_request_received", **request_context)

        if not is_authorized(request.headers, current_app.config["NOTIFIER_TOKEN"]):
            log_event(logger, logging.WARNING, "alert_request_unauthorized", **request_context)
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        try:
            message = extract_message(request)
        except ValueError as exc:
            log_event(
                logger,
                logging.WARNING,
                "alert_request_invalid",
                error=str(exc),
                duration_ms=elapsed_ms(started),
                **request_context,
            )
            return jsonify({"ok": False, "error": str(exc)}), 400

        log_event(
            logger,
            logging.INFO,
            "alert_delivery_started",
            message_length=len(message),
            **request_context,
        )

        try:
            result = current_app.config["ZERNIO_CLIENT"].send_alert(message)
        except Exception as exc:
            zernio_log = getattr(exc, "zernio_log", [])
            log_event(
                logger,
                logging.ERROR,
                "alert_delivery_failed",
                error=str(exc),
                duration_ms=elapsed_ms(started),
                zernio_log=zernio_log,
                **request_context,
            )
            return (
                jsonify({"ok": False, "error": str(exc), "zernio_log": zernio_log}),
                502,
            )

        log_event(
            logger,
            logging.INFO,
            "alert_delivery_succeeded",
            duration_ms=elapsed_ms(started),
            result=result.get("result", {}),
            zernio_log=result.get("zernio_log", []),
            **request_context,
        )
        return jsonify(result)

    @app.post("/start-new-day-conversation")
    def start_new_day_conversation():
        started = time.monotonic()
        request_context = {
            "remote_addr": request.headers.get("X-Forwarded-For", request.remote_addr),
            "content_length": request.content_length,
            "content_type": request.content_type,
        }
        log_event(logger, logging.INFO, "start_conversation_request_received", **request_context)

        if not is_authorized(request.headers, current_app.config["NOTIFIER_TOKEN"]):
            log_event(
                logger,
                logging.WARNING,
                "start_conversation_request_unauthorized",
                **request_context,
            )
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        log_event(logger, logging.INFO, "start_conversation_delivery_started", **request_context)

        try:
            result = current_app.config["ZERNIO_CLIENT"].send_start_new_day_conversation()
        except Exception as exc:
            zernio_log = getattr(exc, "zernio_log", [])
            log_event(
                logger,
                logging.ERROR,
                "start_conversation_delivery_failed",
                error=str(exc),
                duration_ms=elapsed_ms(started),
                zernio_log=zernio_log,
                **request_context,
            )
            return (
                jsonify({"ok": False, "error": str(exc), "zernio_log": zernio_log}),
                502,
            )

        log_event(
            logger,
            logging.INFO,
            "start_conversation_delivery_succeeded",
            duration_ms=elapsed_ms(started),
            result=result.get("result", {}),
            zernio_log=result.get("zernio_log", []),
            **request_context,
        )
        return jsonify(result)

    return app


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("whatsapp_notifier").setLevel(level)


def log_event(
    event_logger: logging.Logger, level: int, event: str, **fields: Any
) -> None:
    event_logger.log(
        level,
        "%s %s",
        event,
        json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str),
    )


def elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def is_authorized(headers: Any, expected_token: str) -> bool:
    if not expected_token:
        return False

    auth_header = headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
    else:
        token = headers.get("X-Notifier-Token", "").strip()

    return bool(token) and hmac.compare_digest(token, expected_token)


def extract_message(req: Any) -> str:
    if req.is_json:
        payload = req.get_json(silent=True)
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
            return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if isinstance(payload, str) and payload.strip():
            return payload.strip()
        if payload is not None:
            return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)

    text = req.get_data(as_text=True).strip()
    if text:
        return text

    raise ValueError("empty body")


app = create_app()
