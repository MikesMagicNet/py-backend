// Paste inside class Handlers in Fiddler's FiddlerScript tab, then Save Script.
// Sends every Epic API host (EOS included) to the backend, like NeoniteV2.

static function OnBeforeRequest(oSession: Session) {
    var h = oSession.hostname;
    var epic = h.EndsWith(".epicgames.com") || h.EndsWith(".epicgames.dev") || h.EndsWith(".epicgames.net");
    var download = h.StartsWith("download") || h.StartsWith("fastly-download") || h.Contains("akamaized");

    if (epic && !download) {
        if (oSession.HTTPMethodIs("CONNECT")) {
            oSession["x-replywithtunnel"] = "BackendTunnel";
            return;
        }
        oSession.oRequest["X-Original-Host"] = h;
        oSession.fullUrl = "http://127.0.0.1:5595" + oSession.PathAndQuery;
    }
}
