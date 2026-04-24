#!/usr/bin/env bash
# First-time setup for a fresh VPS.
# Usage: bash scripts/bootstrap.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  cp .env.example .env
  AUTH=$(openssl rand -hex 32)
  PGPASS=$(openssl rand -hex 16)
  sed -i "s|^AUTH_TOKEN=.*|AUTH_TOKEN=${AUTH}|" .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PGPASS}|" .env
  echo "Generated AUTH_TOKEN=${AUTH}"
  echo "Generated POSTGRES_PASSWORD=${PGPASS}"
  echo
  echo "NOTA: edita .env y añade ANTHROPIC_API_KEY y GEMINI_API_KEY antes de lanzar."
fi

if ! command -v docker >/dev/null; then
  echo "Docker no está instalado. Instalándolo (Ubuntu/Debian)..."
  curl -fsSL https://get.docker.com | sh
fi

echo "Construyendo y arrancando servicios..."
docker compose up -d --build

echo
echo "Servicios arriba. Espera ~60s a que Caddy saque el certificado TLS."
echo "Luego abre: https://who.worldmapsound.com/"
echo
echo "Token de acceso:"
grep ^AUTH_TOKEN .env | cut -d= -f2
