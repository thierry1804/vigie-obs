#!/bin/sh
mkdir -p /logs/logs
while true; do
  echo "{\"level\":\"info\",\"msg\":\"payment processed\",\"service\":\"node-api\"}" >> /logs/logs/app.log
  echo "ERROR express: connection reset" >> /logs/logs/app.log
  sleep 30
done
