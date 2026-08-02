import pytest

from whatsapp_notifier.app import create_app


class FakeZernioClient:
    def __init__(self):
        self.sent_to = []

    def send_alert(self, message):
        self.sent_to.append(message)
        return {
            "ok": True,
            "result": {
                "message_id": "wamid.test",
                "conversation_id": "conversation-test",
                "recipient": "+155...1111",
                "sent_via": "existing_conversation",
            },
            "zernio_log": [
                {
                    "method": "GET",
                    "path": "/inbox/conversations",
                    "status_code": 200,
                    "response": {"data": [{"participantId": "+155...1111"}]},
                },
                {
                    "method": "POST",
                    "path": "/inbox/conversations/conversation-test/messages",
                    "status_code": 200,
                    "response": {"messageId": "wamid.test"},
                },
            ],
        }


class ExplodingZernioClient:
    def send_alert(self, message):
        raise ConnectionError("zernio connection timed out")


@pytest.fixture()
def client():
    fake_zernio = FakeZernioClient()
    app = create_app(
        {
            "TESTING": True,
            "NOTIFIER_TOKEN": "test-token",
            "RECIPIENT_PHONE": "15550001111",
            "ZERNIO_CLIENT": fake_zernio,
        }
    )
    app.fake_zernio = fake_zernio
    return app.test_client()


def test_alert_requires_bearer_token(client):
    response = client.post("/alert", json={"message": "Alerta"})

    assert response.status_code == 401
    assert response.get_json() == {"ok": False, "error": "unauthorized"}


def test_alert_sends_body_message_when_token_is_valid(client):
    response = client.post(
        "/alert",
        json={"message": "Alerta desde tests"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["result"]["message_id"] == "wamid.test"
    assert payload["zernio_log"][0]["path"] == "/inbox/conversations"
    assert client.application.fake_zernio.sent_to == ["Alerta desde tests"]


def test_alert_ignores_recipient_in_request_body(client):
    response = client.post(
        "/alert",
        json={"message": "No debe usar otro numero", "to": "+15551234567"},
        headers={"X-Notifier-Token": "test-token"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result"]["recipient"] == "+155...1111"
    assert client.application.fake_zernio.sent_to == ["No debe usar otro numero"]


def test_alert_serializes_json_body_when_message_is_missing(client):
    response = client.post(
        "/alert",
        json={"severity": "high", "service": "checkout"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert '"severity": "high"' in client.application.fake_zernio.sent_to[0]
    assert '"service": "checkout"' in client.application.fake_zernio.sent_to[0]


def test_create_app_reads_unprefixed_environment_variables(monkeypatch):
    monkeypatch.setenv("NOTIFIER_TOKEN", "env-token")
    monkeypatch.setenv("RECIPIENT_PHONE", "15550001111")
    monkeypatch.setenv("ZERNIO_API_KEY", "env-zernio-key")
    monkeypatch.setenv("ZERNIO_ACCOUNT_ID", "env-account")

    app = create_app()

    assert app.config["NOTIFIER_TOKEN"] == "env-token"
    assert app.config["RECIPIENT_PHONE"] == "15550001111"
    assert app.config["ZERNIO_API_KEY"] == "env-zernio-key"
    assert app.config["ZERNIO_ACCOUNT_ID"] == "env-account"


def test_alert_returns_json_when_zernio_client_raises_unexpected_error():
    app = create_app(
        {
            "TESTING": True,
            "NOTIFIER_TOKEN": "test-token",
            "RECIPIENT_PHONE": "15550001111",
            "ZERNIO_CLIENT": ExplodingZernioClient(),
        }
    )
    client = app.test_client()

    response = client.post(
        "/alert",
        json={"message": "Alerta"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 502
    payload = response.get_json()
    assert payload["ok"] is False
    assert "zernio connection timed out" in payload["error"]
    assert payload["zernio_log"] == []
