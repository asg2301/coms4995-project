#!/bin/sh
set -eu
: > /app/state.lock
[ -f /app/state.json ] || echo '{"users":{},"tokens":{},"workspaces":{},"channels":{},"messages":{},"events":{},"members":{},"dms":{},"reactions":{}}' > /app/state.json
p0=; p1=; p2=
launch() {
  n="$1"; port=$((8000+n))
  NODE_ID="$n" PORT="$port" python3 /app/server.py >/tmp/huddle-node-"$n".log 2>&1 &
  eval "p$n=$!"
}
launch 0; launch 1; launch 2
python3 /app/irc.py >/tmp/huddle-irc.log 2>&1 & ircpid=$!
cleanup() { kill "$p0" "$p1" "$p2" "$ircpid" 2>/dev/null || true; exit 0; }
trap cleanup INT TERM EXIT
while :; do
  for n in 0 1 2; do
    eval "p=\$p$n"
    if ! kill -0 "$p" 2>/dev/null; then
      launch "$n"
    fi
  done
  sleep 1
done
