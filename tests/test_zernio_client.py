import logging

import pytest
import requests

from whatsapp_notifier.zernio_client import ZernioClient, ZernioDeliveryError, sanitize


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class TimeoutSession:
    def request(self, **kwargs):
        raise requests.Timeout("request timed out")


def test_send_alert_posts_to_configured_conversation():
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "data": {
                        "messageId": "wamid.real",
                        "conversationId": "conversation-1",
                    }
                },
            )
        ]
    )
    client = ZernioClient(
        api_key="secret-key",
        base_url="https://zernio.example/api/v1",
        account_id="account-1",
        sender_phone="+12025550100",
        recipient_phone="+15550001111",
        conversation_id="conversation-1",
        session=session,
    )

    result = client.send_alert("CPU alta")

    assert result["ok"] is True
    assert result["result"]["message_id"] == "wamid.real"
    assert result["result"]["conversation_id"] == "conversation-1"
    assert result["result"]["recipient"] == "+155...1111"
    assert result["result"]["sent_via"] == "configured_conversation"
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"].endswith("/inbox/conversations/conversation-1/messages")
    assert session.calls[0]["json"] == {"accountId": "account-1", "message": "CPU alta"}
    assert "Authorization" not in result["zernio_log"][0]


def test_from_config_uses_configured_timeout_seconds():
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "data": {
                        "messageId": "wamid.timeout",
                        "conversationId": "conversation-1",
                    }
                },
            )
        ]
    )
    client = ZernioClient.from_config(
        {
            "ZERNIO_API_KEY": "secret-key",
            "ZERNIO_API_URL": "https://zernio.example/api/v1",
            "ZERNIO_ACCOUNT_ID": "account-1",
            "ZERNIO_SENDER_PHONE": "+12025550100",
            "RECIPIENT_PHONE": "+15550001111",
            "ZERNIO_CONVERSATION_ID": "conversation-1",
            "ZERNIO_TIMEOUT_SECONDS": "75",
            "ZERNIO_SESSION": session,
        }
    )

    client.send_alert("CPU alta")

    assert session.calls[0]["timeout"] == 75


def test_send_start_new_day_conversation_posts_configured_template():
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "data": {
                        "messageId": "wamid.template",
                        "conversationId": "conversation-template",
                    }
                },
            )
        ]
    )
    client = ZernioClient(
        api_key="secret-key",
        base_url="https://zernio.example/api/v1",
        account_id="account-1",
        sender_phone="+12025550100",
        recipient_phone="+15550001111",
        start_template_name="start_new_day_conversation",
        start_template_language="en_US",
        session=session,
    )

    result = client.send_start_new_day_conversation()

    assert result["ok"] is True
    assert result["result"]["message_id"] == "wamid.template"
    assert result["result"]["conversation_id"] == "conversation-template"
    assert result["result"]["sent_via"] == "start_new_day_template"
    assert result["result"]["template_name"] == "start_new_day_conversation"
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"].endswith("/inbox/conversations")
    assert session.calls[0]["json"] == {
        "accountId": "account-1",
        "participantId": "15550001111",
        "templateName": "start_new_day_conversation",
        "templateLanguage": "en_US",
        "templateParams": [],
    }


def test_send_alert_logs_zernio_request_without_credentials(caplog):
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "data": {
                        "messageId": "wamid.logs",
                        "conversationId": "conversation-1",
                    }
                },
            )
        ]
    )
    client = ZernioClient(
        api_key="secret-key",
        base_url="https://zernio.example/api/v1",
        account_id="account-1",
        sender_phone="+12025550100",
        recipient_phone="+15550001111",
        conversation_id="conversation-1",
        session=session,
    )

    with caplog.at_level(logging.INFO, logger="whatsapp_notifier.zernio_client"):
        client.send_alert("Ticket 2026-08-02 1234567890")

    assert "zernio_request_started" in caplog.text
    assert "zernio_request_finished" in caplog.text
    assert "duration_ms" in caplog.text
    assert "Ticket 2026-08-02 1234567890" in caplog.text
    assert "secret-key" not in caplog.text


def test_send_alert_retries_database_unavailable_until_success(monkeypatch):
    sleeps = []
    monkeypatch.setattr(
        "whatsapp_notifier.zernio_client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    session = FakeSession(
        [
            FakeResponse(
                503,
                {
                    "type": "api_error",
                    "code": "temporarily_unavailable",
                    "error": "Temporary service issue while reaching the database.",
                },
            ),
            FakeResponse(
                503,
                {
                    "type": "api_error",
                    "code": "temporarily_unavailable",
                    "error": "Temporary service issue while reaching the database.",
                },
            ),
            FakeResponse(
                200,
                {
                    "data": {
                        "messageId": "wamid.after-retry",
                        "conversationId": "conversation-1",
                    }
                },
            ),
        ]
    )
    client = ZernioClient(
        api_key="secret-key",
        base_url="https://zernio.example/api/v1",
        account_id="account-1",
        sender_phone="+12025550100",
        recipient_phone="+15550001111",
        conversation_id="conversation-1",
        session=session,
    )

    result = client.send_alert("Alerta con retry")

    assert result["ok"] is True
    assert result["result"]["message_id"] == "wamid.after-retry"
    assert len(session.calls) == 3
    assert [entry["attempt"] for entry in result["zernio_log"]] == [1, 2, 3]
    assert [entry["status_code"] for entry in result["zernio_log"]] == [503, 503, 200]
    assert sleeps == [2, 5]


def test_send_alert_does_not_retry_unrelated_503(monkeypatch):
    monkeypatch.setattr(
        "whatsapp_notifier.zernio_client.time.sleep",
        lambda seconds: pytest.fail(f"unexpected sleep: {seconds}"),
    )
    session = FakeSession(
        [
            FakeResponse(
                503,
                {
                    "type": "api_error",
                    "code": "temporarily_unavailable",
                    "error": "Scheduled maintenance.",
                },
            ),
            FakeResponse(
                200,
                {
                    "data": {
                        "messageId": "wamid.should-not-send",
                        "conversationId": "conversation-1",
                    }
                },
            ),
        ]
    )
    client = ZernioClient(
        api_key="secret-key",
        base_url="https://zernio.example/api/v1",
        account_id="account-1",
        sender_phone="+12025550100",
        recipient_phone="+15550001111",
        conversation_id="conversation-1",
        session=session,
    )

    with pytest.raises(ZernioDeliveryError):
        client.send_alert("Alerta sin retry")

    assert len(session.calls) == 1


def test_sanitize_masks_phone_values_without_masking_messages_with_dates():
    assert sanitize("+573113232581") == "+573...2581"
    assert sanitize("573113232581") == "+573...2581"
    assert sanitize({"accountId": "6a180a034c7f364ffded3c9c"}) == {
        "accountId": "6a18...3c9c"
    }
    assert sanitize("Prueba 2026-08-02 1234567890") == (
        "Prueba 2026-08-02 1234567890"
    )


def test_send_alert_finds_existing_whatsapp_conversation_before_sending():
    session = FakeSession(
        [
            FakeResponse(200, {"data": [], "pagination": {"hasMore": False}}),
            FakeResponse(
                200,
                {
                    "data": [
                        {
                            "id": "conversation-2",
                            "accountId": "sandbox-account-mapped",
                            "accountUsername": "+12025550100",
                            "platform": "whatsapp",
                            "participantId": "+15550001111",
                        }
                    ],
                    "pagination": {"hasMore": False},
                },
            ),
            FakeResponse(
                200,
                {
                    "status": "success",
                    "message": {
                        "id": "wamid.found",
                        "conversationId": "conversation-2",
                    },
                },
            ),
        ]
    )
    client = ZernioClient(
        api_key="secret-key",
        base_url="https://zernio.example/api/v1",
        account_id="account-1",
        sender_phone="+12025550100",
        recipient_phone="+15550001111",
        session=session,
    )

    result = client.send_alert("Pago fallido")

    assert result["ok"] is True
    assert result["result"]["message_id"] == "wamid.found"
    assert result["result"]["conversation_id"] == "conversation-2"
    assert result["result"]["sent_via"] == "existing_conversation"
    assert session.calls[0]["params"] == {
        "accountId": "account-1",
        "platform": "whatsapp",
        "limit": 100,
    }
    assert session.calls[1]["params"] == {"platform": "whatsapp", "limit": 100}
    assert session.calls[2]["method"] == "POST"


def test_send_alert_raises_error_with_zernio_log_when_api_fails():
    session = FakeSession(
        [
            FakeResponse(
                400,
                {"code": "TEMPLATE_REQUIRED", "error": "approved template required"},
            )
        ]
    )
    client = ZernioClient(
        api_key="secret-key",
        base_url="https://zernio.example/api/v1",
        account_id="account-1",
        sender_phone="+12025550100",
        recipient_phone="+15550001111",
        conversation_id="conversation-1",
        session=session,
    )

    with pytest.raises(ZernioDeliveryError) as exc_info:
        client.send_alert("Alerta")

    assert "TEMPLATE_REQUIRED" in str(exc_info.value)
    assert exc_info.value.zernio_log[0]["status_code"] == 400
    assert exc_info.value.zernio_log[0]["response"]["error"] == "approved template required"


def test_send_alert_raises_delivery_error_with_log_when_network_fails():
    client = ZernioClient(
        api_key="secret-key",
        base_url="https://zernio.example/api/v1",
        account_id="account-1",
        sender_phone="+12025550100",
        recipient_phone="+15550001111",
        conversation_id="conversation-1",
        session=TimeoutSession(),
    )

    with pytest.raises(ZernioDeliveryError) as exc_info:
        client.send_alert("Alerta")

    assert "request timed out" in str(exc_info.value)
    assert exc_info.value.zernio_log[0]["path"] == "/inbox/conversations/conversation-1/messages"
    assert exc_info.value.zernio_log[0]["status_code"] is None
