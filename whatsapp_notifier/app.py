import hmac
import json
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from flask import Flask, current_app, jsonify, request

from .zernio_client import ZernioClient


def create_app(test_config: Optional[Dict[str, Any]] = None) -> Flask:
    load_dotenv()

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
                "endpoints": ["/health", "/alert"],
            }
        )

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.post("/alert")
    def alert():
        if not is_authorized(request.headers, current_app.config["NOTIFIER_TOKEN"]):
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        try:
            message = extract_message(request)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        try:
            result = current_app.config["ZERNIO_CLIENT"].send_alert(message)
        except Exception as exc:
            zernio_log = getattr(exc, "zernio_log", [])
            return (
                jsonify({"ok": False, "error": str(exc), "zernio_log": zernio_log}),
                502,
            )

        return jsonify(result)

    return app


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
