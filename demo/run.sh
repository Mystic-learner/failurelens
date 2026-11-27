#!/bin/sh
set -e
cd /srv/demo
python server.py &
sleep 1
python worker.py &
exec python -m shipper --file /srv/demo/inventory-sync.log --service "$SHIP_SERVICE" --url "$SHIP_URL" --from-end
