# BRFU Twilio Webhook UAT Receiver

A **test-only** Python 3 webhook receiver for the BRFU-UAT Twilio subaccount, designed for Docker on Raspberry Pi (arm64 or arm/v7) behind Cloudflare Tunnel.

It **never calls BRFU APIs, sends SMS/WhatsApp, or changes real player records**. It validates inbound Twilio signatures, records redacted metadata in SQLite, detects repeated Message SIDs, and returns valid empty TwiML.

## 1. Deploy on Raspberry Pi

```bash
git clone https://github.com/prabinat/brfu-webhook.git
cd brfu-webhook
cp .env.example .env
nano .env
```

Set `TWILIO_AUTH_TOKEN` to the **BRFU-UAT subaccount** live Auth Token (not the parent account token), set `PUBLIC_BASE_URL` to your actual HTTPS Cloudflare Tunnel hostname (for example `https://brfu-webhook.example.com`), and set a long random `ADMIN_TOKEN`. Generate an admin token with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Never commit `.env` or expose it to other users.

```bash
docker compose up -d --build
docker compose ps
curl -i http://127.0.0.1:8088/health
```

If your Pi uses 32-bit OS, use an official Python base image supported by your architecture; `python:3.12-slim` is multi-architecture where available.

## 2. Cloudflare Tunnel

Create an existing Cloudflare Tunnel public hostname **pointing to `http://localhost:8088`** if `cloudflared` runs on the Pi host. If the tunnel is deployed in Docker rather than on the host, put both containers on an appropriate common Docker network and target `http://brfu-webhook:8080` instead. Set `PUBLIC_BASE_URL=https://YOUR-HOSTNAME` (no trailing slash) and restart the app.

Keep the hostname distinct from `brfu.rize.bm`. Never weaken the live BRFU Cloudflare Access configuration just for this receiver. The webhook path must be accessible without interactive Access login, while application-level Twilio signature verification stays mandatory.

Test from an external network:
```bash
curl -i https://YOUR-HOSTNAME/health
curl -i https://YOUR-HOSTNAME/api/webhooks/twilio/whatsapp
```
The webhook GET intentionally returns **405**, which is normal. A POST without valid Twilio signature must return **403**.

## 3. Twilio Sandbox UAT

While switched to **BRFU-UAT** in Twilio Console, open **Messaging > Try it out > Send a WhatsApp message > Sandbox settings**. After verifying `/health`, set **When a message comes in** to:

```text
https://YOUR-HOSTNAME/api/webhooks/twilio/whatsapp
```

Choose **POST**, save, then send `YES FX#25`, `NO FX#25`, and `MAYBE FX#25` from your joined, approved test phone. **This lab does not forward or mutate any BRFU records.** Restore the previous Sandbox demo-reply URL after testing, if needed.

Twilio signs requests using the publicly visible URL and its account Auth Token. If the tunnel URL, protocol, path, port, or token differs from `PUBLIC_BASE_URL` and the configured Twilio callback, the request will return 403. Do not disable signature validation to fix it.

## 4. Inspect events safely

Read the event summary locally from your Pi without revealing `ADMIN_TOKEN` in shell history:

```bash
read -rsp "Admin token: " ADMIN_TOKEN; echo
curl -sS -H "Authorization: Bearer ${ADMIN_TOKEN}" http://127.0.0.1:8088/events
unset ADMIN_TOKEN
```

Only masked sender/recipient phone numbers, limited RSVP words and Message SIDs are stored. The raw message body and authentication header are never stored. SQLite persists in the Docker named volume `brfu-webhook-data`.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Public process health |
| GET | `/events` | Bearer-protected, latest 50 sanitized events |
| GET | `/stats` | Bearer-protected counts |
| POST | `/api/webhooks/twilio/whatsapp` | Twilio signed form POST |
| POST | `/api/match-day-ops/rsvp/webhook` | Same test-only handler |
| GET | webhook paths | 405 Method Not Allowed |

Returns an empty `<Response></Response>` by default, avoiding automatic outbound WhatsApp replies and related charges.

## 5. Security and scope

- Deliberately has **no outbound HTTP clients**, no BRFU credentials, and no path to production RSVP databases.
- Twilio HMAC-SHA1 validation uses the configured public URL and the subaccount Auth Token; compares signature in constant time.
- Rejects unsigned, tampered, overlarge or incorrectly encoded webhook requests.
- Uses a local SQLite store with idempotency on Message SID; no raw phone numbers or arbitrary text retained.
- Admin API is bearer-protected; no public event listing.
- `BRFU_ALLOW_SEND=false` is hard-coded in the container environment and not used as an override. Sending is not implemented.
- Keep the Cloudflare hostname private to your testing workflow, monitor access logs, and remove the public tunnel mapping when testing finishes.
- This is **not** a BRFU production webhook implementation. It demonstrates Twilio -> tunnel -> signed receiver only. It cannot certify live BRFU deployment or safety controls.

## Run automated tests

```bash
docker compose run --rm --no-deps app python -m unittest discover -s tests -v
```

## Optional allowlisted WhatsApp auto-reply

By default the receiver returns empty TwiML. To enable a test-only acknowledgement, set the following in the private .env:

    REPLY_ENABLED=true
    ALLOWED_TEST_SENDERS=whatsapp:+YOUR_OWN_TEST_PHONE
    REPLY_FIXTURES=25

Use the exact inbound From number including whatsapp: and country code. Recreate with docker compose up -d --build --force-recreate. Send a fresh YES FX#25, NO FX#25 or MAYBE FX#25 from the allowlisted number. Twilio will respond with:

    BRFU UAT TEST ONLY: YES received for test fixture 25. No live BRFU records were changed.

This TwiML response causes Twilio to send a message and can incur usage charges. No reply is generated for unrecognized commands, unlisted fixtures, non-allowlisted numbers or duplicate Message SIDs. Set REPLY_ENABLED=false and recreate the container to turn it off. The application never connects to or changes live BRFU records.
