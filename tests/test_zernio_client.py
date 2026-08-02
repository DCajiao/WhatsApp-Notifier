import pytest

from whatsapp_notifier.zernio_client import ZernioClient, ZernioDeliveryError


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
