"""One-time YouTube login for the bot: prints the refresh token that goes into the YT_REFRESH_TOKEN GitHub Secret.

Run it on your own PC:  python tools/youtube_auth.py
1. Paste the OAuth Client ID and Client secret (Google Cloud -> Credentials -> your "Desktop app" client).
2. A browser opens: sign in with the Google account that owns the YouTube channel and press Allow.
3. Copy the printed refresh token into GitHub -> Settings -> Secrets -> Actions as YT_REFRESH_TOKEN
   (and the Client ID / secret as YT_CLIENT_ID / YT_CLIENT_SECRET). Never paste them in a chat.
"""
import http.server
import json
import secrets
import urllib.parse
import urllib.request
import webbrowser

PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def main():
    client_id = input("Client ID: ").strip()
    client_secret = input("Client secret: ").strip()
    state = secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state})
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("Done - you can close this tab and go back to the terminal.".encode())

        def log_message(self, *args):
            pass

    print("\nOpening the browser. If it doesn't open, copy this link into it:\n" + url + "\n")
    webbrowser.open(url)
    with http.server.HTTPServer(("127.0.0.1", PORT), Handler) as server:
        while "code" not in got and "error" not in got:
            server.handle_request()
    if got.get("error") or got.get("state") != state:
        raise SystemExit(f"Login failed: {got.get('error', 'state mismatch')}")
    data = urllib.parse.urlencode({"code": got["code"], "client_id": client_id, "client_secret": client_secret,
                                   "redirect_uri": REDIRECT, "grant_type": "authorization_code"}).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data, timeout=30) as r:
        token = json.load(r)
    if "refresh_token" not in token:
        raise SystemExit("No refresh token came back. Remove the app's access at myaccount.google.com/permissions "
                         "and run this again.")
    print("\nYT_REFRESH_TOKEN (put it in GitHub Secrets, don't share it):\n\n" + token["refresh_token"] + "\n")


if __name__ == "__main__":
    main()
