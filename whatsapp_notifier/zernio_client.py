import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests


class ZernioDeliveryError(RuntimeError):
    def __init__(self, message: str, zernio_log: List[Dict[str, Any]]) -> None:
        self.zernio_log = zernio_log
        super().__init__(message)


class ZernioClient:
    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "ZernioClient":
        return cls(
            api_key=config.get("ZERNIO_API_KEY", ""),
            base_url=config.get("ZERNIO_API_URL", "https://zernio.com/api/v1"),
            account_id=config.get("ZERNIO_ACCOUNT_ID", ""),
            sender_phone=config.get("ZERNIO_SENDER_PHONE", ""),
            recipient_phone=config.get("RECIPIENT_PHONE", ""),
            conversation_id=config.get("ZERNIO_CONVERSATION_ID", ""),
        )

    def __init__(
        self,
        api_key: str,
        base_url: str,
        account_id: str,
        sender_phone: str,
        recipient_phone: str,
        conversation_id: str = "",
        session: Optional[requests.Session] = None,
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.sender_phone = sender_phone
        self.recipient_phone = recipient_phone
        self.conversation_id = conversation_id
        self.session = session or requests.Session()
        self.timeout = timeout
        self.zernio_log: List[Dict[str, Any]] = []

    def send_alert(self, message: str) -> Dict[str, Any]:
        self.zernio_log = []
        self._validate_config()

        conversation_id = self.conversation_id
        sent_via = "configured_conversation"
        if not conversation_id:
            conversation = self._find_existing_conversation()
            if conversation:
                conversation_id = str(conversation.get("id") or conversation.get("conversationId"))
                sent_via = "existing_conversation"

        if conversation_id:
            payload = self._request(
                "POST",
                f"/inbox/conversations/{quote(conversation_id, safe='')}/messages",
                json_body={"accountId": self.account_id, "message": message},
            )
        else:
            payload = self._request(
                "POST",
                "/inbox/conversations",
                json_body={
                    "accountId": self.account_id,
                    "participantId": digits_only(self.recipient_phone),
                    "message": message,
                },
            )
            sent_via = "new_conversation"

        message_id, response_conversation_id = extract_send_result(payload)
        return {
            "ok": True,
            "result": {
                "message_id": message_id,
                "conversation_id": response_conversation_id or conversation_id,
                "recipient": mask_phone(self.recipient_phone),
                "sent_via": sent_via,
            },
            "zernio_log": deepcopy(self.zernio_log),
        }

    def _validate_config(self) -> None:
        missing = []
        if not self.api_key:
            missing.append("ZERNIO_API_KEY")
        if not self.account_id:
            missing.append("ZERNIO_ACCOUNT_ID")
        if len(digits_only(self.recipient_phone)) < 8:
            missing.append("RECIPIENT_PHONE")
        if missing:
            raise ZernioDeliveryError(
                f"missing configuration: {', '.join(missing)}",
                deepcopy(self.zernio_log),
            )

    def _find_existing_conversation(self) -> Optional[Dict[str, Any]]:
        queries = [
            {"accountId": self.account_id, "platform": "whatsapp", "limit": 100},
            {"platform": "whatsapp", "limit": 100},
            {"accountId": self.account_id, "limit": 100},
            {"limit": 100},
        ]
        seen_ids = set()

        for query in queries:
            conversations = self._list_conversations(query)
            for conversation in conversations:
                conversation_id = str(
                    conversation.get("id") or conversation.get("conversationId") or ""
                )
                if conversation_id in seen_ids:
                    continue
                seen_ids.add(conversation_id)
                if self._conversation_matches(conversation):
                    return conversation

        return None

    def _list_conversations(self, initial_query: Dict[str, Any]) -> List[Dict[str, Any]]:
        query = dict(initial_query)
        conversations: List[Dict[str, Any]] = []

        while True:
            payload = self._request("GET", "/inbox/conversations", params=query)
            page = payload.get("data") or payload.get("conversations") or []
            if isinstance(page, list):
                conversations.extend(page)

            pagination = payload.get("pagination") or {}
            cursor = pagination.get("nextCursor")
            if not pagination.get("hasMore") or not cursor:
                break
            query["cursor"] = cursor

        return conversations

    def _conversation_matches(self, conversation: Dict[str, Any]) -> bool:
        if conversation.get("platform") and conversation.get("platform") != "whatsapp":
            return False

        participant_values = [
            conversation.get("participantId"),
            conversation.get("participantPhone"),
            conversation.get("participantUsername"),
            conversation.get("participantName"),
        ]
        recipient_digits = digits_only(self.recipient_phone)
        if not any(digits_only(value) == recipient_digits for value in participant_values):
            return False

        account_values = [
            conversation.get("accountId"),
            conversation.get("socialAccountId"),
            conversation.get("account_id"),
        ]
        if any(str(value or "") == self.account_id for value in account_values):
            return True

        sender_digits = digits_only(self.sender_phone)
        sender_values = [
            conversation.get("accountUsername"),
            conversation.get("accountPhone"),
            conversation.get("senderPhoneNumber"),
        ]
        return bool(sender_digits) and any(
            digits_only(value) == sender_digits for value in sender_values
        )

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        response = self.session.request(
            method=method,
            url=url,
            params={key: value for key, value in (params or {}).items() if value is not None},
            json=json_body,
            headers=headers,
            timeout=self.timeout,
        )
        payload = parse_response(response)

        log_entry = {
            "method": method,
            "path": path,
            "params": sanitize(params or {}),
            "request_body": sanitize(json_body or {}),
            "status_code": response.status_code,
            "response": sanitize(payload),
        }
        self.zernio_log.append(log_entry)

        if response.status_code >= 400:
            raise ZernioDeliveryError(
                format_zernio_error(response.status_code, payload),
                deepcopy(self.zernio_log),
            )

        return payload


def digits_only(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def mask_phone(value: Any) -> str:
    digits = digits_only(value)
    if len(digits) < 8:
        return ""
    return f"+{digits[:3]}...{digits[-4:]}"


def parse_response(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def sanitize(value: Any) -> Any:
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {
            key: sanitize(item)
            for key, item in value.items()
            if key.lower() not in {"authorization", "api_key", "apikey", "token"}
        }
    if isinstance(value, (bool, int, float)) or value is None:
        return value

    text = str(value)
    if text.startswith("wamid."):
        return text
    digits = digits_only(text)
    if len(digits) >= 8 and (text.startswith("+") or len(digits) >= 10):
        return mask_phone(text)
    return text


def extract_send_result(payload: Any) -> Tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}

    message_id = (
        data.get("messageId")
        or data.get("id")
        or message.get("messageId")
        or message.get("id")
        or payload.get("messageId")
        or payload.get("id")
        or ""
    )
    conversation_id = (
        data.get("conversationId")
        or message.get("conversationId")
        or payload.get("conversationId")
        or ""
    )
    return str(message_id), str(conversation_id)


def format_zernio_error(status_code: int, payload: Any) -> str:
    if isinstance(payload, dict):
        code = payload.get("code")
        error = payload.get("error") or payload.get("message")
        if code and error:
            return f"Zernio HTTP {status_code}: {code} - {error}"
        if error:
            return f"Zernio HTTP {status_code}: {error}"
    return f"Zernio HTTP {status_code}: {payload}"
