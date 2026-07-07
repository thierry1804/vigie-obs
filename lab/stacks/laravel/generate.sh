#!/bin/sh
mkdir -p /logs/storage/logs
while true; do
  echo "[$(date -Iseconds)] local.INFO: Invoice payment received for order $RANDOM" >> /logs/storage/logs/laravel.log
  echo "{\"message\":\"Order created\",\"level\":\"info\"}" >> /logs/storage/logs/laravel.log
  sleep 30
done
