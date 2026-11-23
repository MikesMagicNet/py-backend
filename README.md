# Halcyon Dev Backend

Python backend in the style of NeoniteV2.

## Run

```
pip install -r requirements.txt
python run.py
```

Settings live in `config.json` (port 5595 by default, like Neonite).

## Redirect (like NeoniteV2)

Newer builds also log in through the EOS SDK (`api.epicgames.dev`), which a game-only hook misses.
A system proxy catches it, so use one of these:

**Fiddler Classic**
1. Tools > Options > HTTPS: tick *Decrypt HTTPS traffic* and trust the root certificate.
2. Open the FiddlerScript tab, paste `proxy/FiddlerScript.js` inside `class Handlers`
   (replace the existing `OnBeforeRequest`), and Save Script.
3. Launch the game.

**mitmproxy (Python)**
1. `pip install mitmproxy`
2. `mitmdump -s proxy/redirect.py --listen-port 8080`
3. Set the Windows proxy to `127.0.0.1:8080`, open http://mitm.it and install the certificate.
4. Launch the game.

Download hosts (`download*.epicgames.com`, Akamai, Fastly) are left alone so install bundles still load.

## Debugging

- `requests.log`: every request and its status.
- `datarouter.log`: the game's own telemetry, including `LoginStepFailure` events with the failing step.

## Social

Friends, parties, presence and chat run on the same port as the backend.

- **XMPP** (`xmpp-service-prod.ol.epicgames.com`): presence, friend and party notifications. Log: `xmpp.log`.
- **Epic Connect / STOMP** (`connect.epicgames.dev`): live chat delivery for the EOS SDK. Log: `stomp.log`.
- **Chat REST** (`/epic/chat/v1/...`): whispers, DMs, party and group conversations. Bodies logged to `chat.log`.

Both sockets arrive through the proxy on `/`; the websocket subprotocol (`xmpp` or `v1x.stomp`) picks the handler.

State lives in `data/accounts.json`, `data/social.json` (friends, blocks) and `data/chat.json`. Parties are memory only.
`autoCreateAccounts` in `config.json` creates an account on first login; turn it off once accounts come from the Discord bot.
