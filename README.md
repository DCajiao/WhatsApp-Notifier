# WhatsApp Notifier

API pequeña en Flask para recibir una alerta HTTP y enviarla a un único número
de WhatsApp usando Zernio. Está pensada para casos como monitoreo, alertas
internas o integraciones simples donde un sistema externo hace `POST /alert`.
También incluye `POST /start-new-day-conversation` para enviar una plantilla
aprobada que reabre la conversación diaria de WhatsApp.

## Cómo funciona

1. El cliente llama `POST /alert` con un token privado.
2. La API toma el texto desde `message`; si no existe, serializa el JSON completo.
3. El destinatario siempre sale de `RECIPIENT_PHONE`, nunca del body.
4. El cliente de Zernio busca una conversación de Inbox existente y envía el texto.
5. La respuesta incluye el resultado del envío y un `zernio_log` sanitizado.

Cuando la ventana de 24 horas de WhatsApp está cerrada, llama
`POST /start-new-day-conversation`. Ese endpoint envía el template
`start_new_day_conversation`: “Nuevo día, nuevas notificaciones. Presiona
aceptar para recibir notificaciones hoy”, con botón `Aceptar`. Después de que
el destinatario responda, `POST /alert` vuelve a poder enviar texto libre.

## Sobre Zernio

Zernio expone una API para conectar cuentas de WhatsApp e interactuar con Inbox.
Este proyecto usa `ZERNIO_API_KEY`, `ZERNIO_ACCOUNT_ID` y, opcionalmente,
`ZERNIO_CONVERSATION_ID`. Si no configuras `ZERNIO_CONVERSATION_ID`, el servicio
busca la conversación existente por WhatsApp, remitente y destinatario.

WhatsApp exige que el destinatario haya abierto una ventana de conversación válida
o que uses plantillas aprobadas para iniciar/reabrir conversaciones. Este servicio
envía texto libre a una conversación existente.

## Variables de entorno

Crea un `.env` local:

```bash
cp .env.example .env
```

Configura:

```bash
NOTIFIER_TOKEN=replace-with-a-long-random-token
ZERNIO_API_KEY=sk_replace_me
ZERNIO_API_URL=https://zernio.com/api/v1
ZERNIO_ACCOUNT_ID=replace-with-your-zernio-whatsapp-account-id
ZERNIO_SENDER_PHONE=replace-with-your-zernio-whatsapp-sender
ZERNIO_CONVERSATION_ID=
ZERNIO_TIMEOUT_SECONDS=70
ZERNIO_START_TEMPLATE_NAME=start_new_day_conversation
ZERNIO_START_TEMPLATE_LANGUAGE=en_US
LOG_LEVEL=INFO
RECIPIENT_PHONE=replace-with-recipient-phone-international-format
```

Genera un token:

```bash
openssl rand -hex 32
```

No subas `.env` a git. El repo solo incluye `.env.example` con placeholders.

## Docker

Construir y correr:

```bash
docker compose up --build
```

La API queda en:

```text
http://localhost:8000
```

El contenedor corre Gunicorn con 2 workers, 4 threads por worker y timeout
desactivado porque el cliente reintenta indefinidamente cuando Zernio responde
que no puede alcanzar su base de datos. Los access logs salen por stdout.

## Logs

La app escribe logs estructurados por stdout, por lo que Render los muestra
directamente en el panel del servicio. Para diagnosticar Zernio, busca eventos
como `zernio_request_started`, `zernio_request_finished`,
`zernio_request_retrying`, `alert_delivery_succeeded` y
`alert_delivery_failed`. Los logs incluyen intento, path, duracion, status code
y respuesta sanitizada; no incluyen tokens ni API keys.

Probar healthcheck:

```bash
curl http://localhost:8000/health
```

Enviar alerta:

```bash
curl -X POST http://localhost:8000/alert \
  -H "Authorization: Bearer $NOTIFIER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"Alerta desde Docker"}'
```

Enviar template para iniciar el día:

```bash
curl -X POST http://localhost:8000/start-new-day-conversation \
  -H "Authorization: Bearer $NOTIFIER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Respuesta esperada:

```json
{
  "ok": true,
  "result": {
    "message_id": "wamid...",
    "conversation_id": "conversation-id",
    "recipient": "+155...1111",
    "sent_via": "existing_conversation"
  },
  "zernio_log": []
}
```

## Desarrollo local

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
flask --app whatsapp_notifier.app run --host 0.0.0.0 --port 8000
```
