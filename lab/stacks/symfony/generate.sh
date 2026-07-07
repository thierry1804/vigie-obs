#!/bin/sh
mkdir -p /logs/var/log
while true; do
  echo "{\"level\":\"info\",\"message\":\"Commande créée order_id=$((RANDOM))\",\"timestamp\":\"$(date -Iseconds)\"}" >> /logs/var/log/app.log
  echo "[$(date -Iseconds)] app.ERROR: timeout connexion base" >> /logs/var/log/prod.log
  sleep 30
done
