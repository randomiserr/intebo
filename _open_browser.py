"""
Maly pomocnik pro start.bat: caka, az server zacne odpovidat na zadanou URL,
a teprve potom otevre prohlizec. Pokud server nenabehne do timeoutu,
browser se neotevre (predejde se otevreni connection error stranky).

Pouziti:
    python _open_browser.py http://localhost:8000/
"""
import sys
import time
import urllib.error
import urllib.request
import webbrowser

TIMEOUT_SECONDS = 30
POLL_INTERVAL = 0.5

def main() -> int:
    if len(sys.argv) < 2:
        return 1

    url = sys.argv[1]
    deadline = time.monotonic() + TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status < 500:
                    webbrowser.open(url)
                    return 0
        except urllib.error.HTTPError as exc:
            # HTTP 503 is the intentional "connect VPN and retry" page. The
            # local server is ready, so show that useful response immediately.
            if exc.code == 503:
                webbrowser.open(url)
                return 0
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)

    # Server nenabehl - tise skoncime, at uzivatele nemate connection error.
    return 1

if __name__ == "__main__":
    sys.exit(main())
