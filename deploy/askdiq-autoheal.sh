#!/bin/sh
# Restart AskDIQ containers that Docker reports as unhealthy.
# Docker's restart policy only covers crashes; a hung-but-alive process stays
# "unhealthy" until something restarts it. Run every minute from cron:
#   * * * * * /opt/askdiq/deploy-kit/askdiq-autoheal.sh >> /var/log/askdiq-autoheal.log 2>&1
# Only containers whose name starts with "askdiq-" are touched.
set -eu
for id in $(docker ps --filter "name=^askdiq-" --filter "health=unhealthy" --format '{{.ID}}'); do
  name=$(docker inspect --format '{{.Name}}' "$id" | sed 's#^/##')
  echo "$(date -Is) restarting unhealthy container $name"
  docker restart --time 20 "$id"
done
