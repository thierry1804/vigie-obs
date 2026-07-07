#!/bin/sh
# Désinstallation labo VIGIE — sans trace sur le projet observé
docker compose -f lab/docker-compose.lab.yml down -v 2>/dev/null || true
docker compose down -v 2>/dev/null || true
echo "Stack VIGIE arrêtée. Aucune modification applicative à annuler (zéro code)."
