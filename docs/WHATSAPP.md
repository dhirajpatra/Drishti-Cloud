# WhatsApp alerts for the hackathon (free test setup)

Drishti sends alerts through Meta's **WhatsApp Business Cloud API**. For a hackathon you do not need a business, a paid number or
Docker changes: Meta gives every developer app a **free test number** you can message up to a handful of your own phones from.
Meta's screens and limits change from time to time, so treat the menu names below as a guide and the official page as the truth:
<https://developers.facebook.com/docs/whatsapp/cloud-api/get-started>

## 1. Create the app (about 10 minutes)
1. Go to <https://developers.facebook.com/apps>, log in with your Facebook account, **Create app**, use case **Other / Business**
   (a Business app), give it a name like `Drishti Alerts`.
2. In the app dashboard **Add product -> WhatsApp -> Set up**. Meta creates a test WhatsApp Business account and a **test phone number** for you.
3. Open **WhatsApp -> API Setup**. You will see:
   - **Temporary access token** (valid about 24 hours),
   - **Phone number ID** (digits; this is *not* the phone number itself),
   - the **From** test number and a **To** box.
4. In the **To** box choose **Manage phone number list**, add *your own* WhatsApp number (country code, no `+`) and confirm the code WhatsApp sends you.
   Add every family/security number you want to alert (the test setup allows only a few recipients).

## 2. Put it in `.env`
```
WHATSAPP_TOKEN=<temporary access token>
WHATSAPP_PHONE_ID=<Phone number ID>
WHATSAPP_TO=919876543210,918888888888        # international format, digits only, no +
WHATSAPP_TEMPLATE=                            # leave EMPTY for the test number (see below)
```
Then `docker compose up -d --build` (or restart `action-agent` and `whatsapp-connector`).
You can also set the numbers per site from the dashboard: **Admin -> site -> Notifications -> WhatsApp numbers**
(the token and phone id always come from `.env`).

## 3. The one rule that surprises everyone: the 24-hour window
WhatsApp only lets a business send **free-text** messages to a person who **messaged the business number in the last 24 hours**.
For the test number that means: **from each phone, send "hi" to the test number** (its number is shown on the API Setup page; save it as a contact).
That opens a 24-hour window, and Drishti's alerts then arrive. Send "hi" again each day of the demo, or just before you present.
Messages outside the window are rejected by Meta and Drishti logs `WhatsApp to ...1234 failed`; the dashboard notification and the webhook fallback still fire.

Templates can message people any time, but they must be approved, and the sample `hello_world` template has **no variable**, so it cannot carry an alert text.
If you later create your own template with exactly one body variable (for example `Drishti alert: {{1}}`), put its name in `WHATSAPP_TEMPLATE`
(and `WHATSAPP_TEMPLATE_LANG`, default `en`). Do that when you move off the test number.

## 4. Test it without waiting for an alert
```bash
curl -s -X POST "https://graph.facebook.com/v21.0/$WHATSAPP_PHONE_ID/messages" \
  -H "Authorization: Bearer $WHATSAPP_TOKEN" -H "Content-Type: application/json" \
  -d '{"messaging_product":"whatsapp","to":"919876543210","type":"text","text":{"body":"Drishti test: hello"}}'
```
A JSON answer with `"messages":[{"id":"wamid..."}]` means it worked. Common errors:

| Error | Meaning / fix |
|-------|---------------|
| `(#131030) Recipient phone number not in allowed list` | add the number under API Setup -> To -> Manage phone number list |
| `(#131047) Re-engagement message` | outside the 24 h window: send "hi" to the test number from that phone |
| `OAuthException 190` / `Session has expired` | the temporary token expired (24 h): generate a new one or make a permanent one (below) |
| `(#100) Invalid parameter` on a template | template name/language wrong, or it does not have exactly one body variable |

Real alerts: trigger one from the dashboard (**Send test frame** with a vertical's sample) or run the offline simulator, and watch
`docker compose logs -f action-agent whatsapp-connector`.

## 5. A token that does not expire after a day (do this before the demo)
1. <https://business.facebook.com/settings> -> **Users -> System users -> Add** (role Admin).
2. **Add assets**: your app (full control) and the WhatsApp account.
3. **Generate new token**: pick your app, tick `whatsapp_business_messaging` and `whatsapp_business_management`, no expiry.
4. Paste it into `WHATSAPP_TOKEN`. Keep it secret: anyone holding it can send messages from your number.

## What it costs
The test number is for testing and is free to use for this kind of demo. Going live means your own number, your own approved templates and Meta's
per-message pricing, which depends on the country and message type; check <https://developers.facebook.com/docs/whatsapp/pricing> before you rely on it.
Drishti sends only for confirmed ALERT/CRITICAL events (never for idle frames) and de-duplicates with a cooldown, so volume stays low.

## If you cannot use WhatsApp at all
Set `NOTIFY_WEBHOOK_URL` to a Slack/Teams/Discord-compatible incoming webhook, or per site in **Admin -> Notifications -> Webhook**.
Dashboard notifications always work with no setup.
