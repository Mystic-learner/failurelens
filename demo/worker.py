import datetime
import os
import time
import urllib.error
import urllib.request

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory-sync.log")
URL = os.environ.get("CHECK_URL", "http://localhost:9999/health")


def log(level, msg):
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts} {level} {msg}\n")


log("INFO", "inventory sync worker started, INVENTORY_URL=" + URL)
while True:
    try:
        urllib.request.urlopen(URL, timeout=3).read()
        log("INFO", "inventory sync ok: GET /health 200")
    except urllib.error.HTTPError as e:
        log("ERROR", f"inventory sync failed: GET /health {e.code}")
        log("ERROR", "order batch aborted: inventory service returning errors")
    except Exception as e:
        log("ERROR", f"inventory sync failed: {e}")
        log("ERROR", "order batch aborted: inventory service unreachable")
    time.sleep(2)
