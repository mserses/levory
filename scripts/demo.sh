#!/usr/bin/env bash
# ServisZeka DEMO (Docker'sız): SQLite + uv + npm ile API ve portal, SENTETİK Türkçe demo verisi.
#
#   ./scripts/demo.sh            # tohumla, API'yi (:8000) ve portalı (:5173) başlat; Ctrl+C durdurur
#   ./scripts/demo.sh --check    # duman testi: başlat, /health ve portal kökünü doğrula, durdur
#   DEMO_RESET=1 ./scripts/demo.sh   # demo veritabanını silip baştan tohumla
#
# Ortam: API_PORT (8000), PORTAL_PORT (5173), DEMO_DIR (<serviszeka>/.demo).
# YALNIZCA DEMO / GELİŞTİRME: imzasız dev kimlik doğrulaması, sahte LLM, bellek içi sahte dizin.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${API_PORT:-8000}"
PORTAL_PORT="${PORTAL_PORT:-5173}"
DEMO_DIR="${DEMO_DIR:-$ROOT/.demo}"
CHECK=0
case "${1:-}" in
  --check) CHECK=1 ;;
  "") ;;
  -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
  *) echo "Bilinmeyen seçenek: $1 (--check | --help)" >&2; exit 2 ;;
esac

for tool in uv npm curl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "HATA: '$tool' bulunamadı." >&2; exit 1; }
done

mkdir -p "$DEMO_DIR"
DB="$DEMO_DIR/serviszeka-demo.db"
if [[ "${DEMO_RESET:-0}" == "1" ]]; then
  rm -f "$DB"
fi

export SZ_ENVIRONMENT=development
export SZ_AUTH_MODE=dev
export SZ_LLM_PROVIDER=fake
export SZ_ANTHROPIC_API_KEY=
export SZ_IDENTITY_PROVIDER=memory
export SZ_CONNECTOR_PROVIDER=none
export SZ_IDENTITY_STANDARD_GROUPS=sg-yazilim-standart,sg-vpn-kullanicilari,m365-ekip-proje-x
export SZ_AI_SHADOW_MODE=false
export SZ_SCHEDULER_ENABLED=true
export SZ_DATABASE_URL="sqlite+aiosqlite:///$DB"
export SZ_AUTO_CREATE_SCHEMA=true
export SZ_CORS_ORIGINS="http://localhost:$PORTAL_PORT,http://127.0.0.1:$PORTAL_PORT"
export SZ_EVENTS_WEBHOOK_SECRET=demo-olay-gizli
# .env'deki ITSM/bildirim ayarları demoya sızmasın (ağ yok)
export SZ_ONCALL_WEBHOOK_URL= SZ_SERVICENOW_INSTANCE_URL= SZ_JIRA_BASE_URL= SZ_NOTIFIER_PROVIDER= \
  SZ_SLACK_BOT_TOKEN= SZ_SLACK_WEBHOOK_URL=

cd "$ROOT"
echo "==> Demo verisi ($DB)"
uv run python -m serviszeka.demo_seed

PIDS=()
kill_tree() {  # uv/npm alt süreçleri (python, vite) de dursun
  local child
  for child in $(pgrep -P "$1" 2>/dev/null || true); do kill_tree "$child"; done
  kill "$1" 2>/dev/null || true
}
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill_tree "$pid"
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait_for() {  # wait_for <url> <ad> <deneme>
  local i
  for ((i = 0; i < $3; i++)); do
    if curl -fs -o /dev/null "$1"; then return 0; fi
    sleep 1
  done
  echo "HATA: $2 yanıt vermedi ($1). Günlük: $DEMO_DIR" >&2
  return 1
}

echo "==> API http://localhost:$API_PORT (günlük: $DEMO_DIR/api.log)"
uv run uvicorn serviszeka.main:app --host 127.0.0.1 --port "$API_PORT" >"$DEMO_DIR/api.log" 2>&1 &
PIDS+=($!)

cd "$ROOT/portal"
if [[ ! -d node_modules ]]; then
  echo "==> Portal bağımlılıkları (npm ci)"
  npm ci --no-audit --no-fund
fi
echo "==> Portal http://localhost:$PORTAL_PORT (günlük: $DEMO_DIR/portal.log)"
VITE_AUTH_MODE=dev VITE_API_BASE_URL="http://localhost:$API_PORT" VITE_CACHE_DIR="$DEMO_DIR/vite" \
  npm run dev -- --host 127.0.0.1 --port "$PORTAL_PORT" --strictPort >"$DEMO_DIR/portal.log" 2>&1 &
PIDS+=($!)
cd "$ROOT"

wait_for "http://127.0.0.1:$API_PORT/health" "API" 60
wait_for "http://127.0.0.1:$PORTAL_PORT/" "Portal" 90

if [[ "$CHECK" == "1" ]]; then
  echo "health: $(curl -fsS "http://127.0.0.1:$API_PORT/health")"
  title="$(curl -fsS "http://127.0.0.1:$PORTAL_PORT/" | grep -o '<title>[^<]*</title>' || true)"
  [[ -n "$title" ]] || { echo "HATA: portal kökü HTML döndürmedi." >&2; exit 1; }
  echo "portal: $title"
  echo "DEMO DUMAN TESTİ GEÇTİ"
  exit 0
fi

cat <<MSG

ServisZeka demo hazır (SENTETİK veri):
  Portal : http://localhost:$PORTAL_PORT/giris
  API    : http://localhost:$API_PORT/docs
  Rehber : $ROOT/DEMO.md (10 dakikalık tur)
Durdurmak için Ctrl+C.
MSG
wait
