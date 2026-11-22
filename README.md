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
