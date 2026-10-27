#!/usr/bin/env bash
# Установка/обновление синхронизации нового магазина Uzum «ComFore shop» (138842)
# с основным МойСклад на VPS — в /root/uzum-supply, рядом с текущей программой.
#
# Запуск на сервере (под root):
#   curl -fsSL https://raw.githubusercontent.com/lex218/uzum---MC/claude/uzum-moysklad-sync-7c7186/deploy/install.sh | bash
# Повторный запуск = обновление кода (настройки и база сохраняются).
#
# Переменные для неинтерактивного запуска: UZUM_TOKEN, MS_TOKEN, ASSUME_YES=1,
# BASE (по умолчанию /root/uzum-supply), SKIP_SYSTEMD=1.
set -euo pipefail

BASE="${BASE:-/root/uzum-supply}"
DIR="$BASE/comfore-shop-sync"
REPO="https://github.com/lex218/uzum---MC.git"
BRANCH="claude/uzum-moysklad-sync-7c7186"
SERVICE="uzum-comfore-shop-sync"
SHOP_ID=138842
UZ_API="https://api-seller.uzum.uz/api/seller-openapi"
MS_API="https://api.moysklad.ru/api/remap/1.2"

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }
# при запуске через «curl | bash» stdin занят скриптом — вопросы читаем с терминала
ask()  { local v; read -r -p "$1" v </dev/tty || true; printf '%s' "$v"; }
asks() { local v; read -r -s -p "$1" v </dev/tty || true; echo >/dev/tty; printf '%s' "$v"; }

say "1/6 Проверка системы"
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }
command -v python3 >/dev/null || fail "нет python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "нужен Python 3.10+, установлен $(python3 -V)"
python3 -m venv --help >/dev/null 2>&1 || apt-get install -y -qq python3-venv
mkdir -p "$BASE"

say "2/6 Код: $DIR (ветка $BRANCH)"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH"
  git -C "$DIR" checkout -q "$BRANCH"
  git -C "$DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q -b "$BRANCH" "$REPO" "$DIR"
fi
echo "версия: $(git -C "$DIR" log --oneline -1)"

say "3/6 Зависимости"
[ -x "$DIR/.venv/bin/python" ] || python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip >/dev/null
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

say "4/6 Ключи"
uz_sees_shop() {
  curl -fsS --max-time 20 -H "Authorization: $1" "$UZ_API/v1/shops" 2>/dev/null \
    | grep -q "\"id\":$SHOP_ID"
}
ms_account() {
  curl -fsS --compressed --max-time 20 -H "Authorization: Bearer $1" \
    -H "Accept-Encoding: gzip" "$MS_API/context/employee" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("uid",""))' 2>/dev/null
}
envget() { [ -f "$1" ] && grep -E "^$2=" "$1" | tail -1 | cut -d= -f2- || true; }

if [ -f "$DIR/.env" ]; then
  UZUM_TOKEN="${UZUM_TOKEN:-$(envget "$DIR/.env" UZUM_TOKEN)}"
  MS_TOKEN="${MS_TOKEN:-$(envget "$DIR/.env" MS_TOKEN)}"
fi
# пробуем ключи текущей серверной программы, чтобы не вводить их заново
if [ -z "${UZUM_TOKEN:-}" ] && [ -f "$BASE/.env" ]; then
  for k in UZUM_TOKEN UZUM_API_KEY UZUM_KEY UZUM_API_TOKEN; do
    v="$(envget "$BASE/.env" "$k")"; [ -n "$v" ] && uz_sees_shop "$v" && { UZUM_TOKEN="$v"; break; }
  done
fi
if [ -z "${MS_TOKEN:-}" ] && [ -f "$BASE/.env" ]; then
  for k in MS_TOKEN MOYSKLAD_TOKEN MOYSKLAD_API_TOKEN MS_API_TOKEN; do
    v="$(envget "$BASE/.env" "$k")"; [ -n "$v" ] && [ -n "$(ms_account "$v")" ] && { MS_TOKEN="$v"; break; }
  done
fi
while [ -z "${UZUM_TOKEN:-}" ] || ! uz_sees_shop "$UZUM_TOKEN"; do
  [ -n "${UZUM_TOKEN:-}" ] && echo "ключ Uzum не видит магазин $SHOP_ID"
  UZUM_TOKEN="$(asks 'Ключ API Uzum (видит магазин ComFore shop): ')"
  [ -z "$UZUM_TOKEN" ] && fail "ключ Uzum не введён"
done
echo "Uzum: ключ видит магазин $SHOP_ID — ок"
while [ -z "${MS_TOKEN:-}" ] || [ -z "$(ms_account "$MS_TOKEN")" ]; do
  [ -n "${MS_TOKEN:-}" ] && echo "токен МойСклад не подошёл"
  MS_TOKEN="$(asks 'Токен МойСклад: ')"
  [ -z "$MS_TOKEN" ] && fail "токен МойСклад не введён"
done
echo "МойСклад: аккаунт $(ms_account "$MS_TOKEN") — ок"

umask 077
cat > "$DIR/.env" <<EOF
UZUM_TOKEN=$UZUM_TOKEN
UZUM_SHOP_ID=$SHOP_ID
MS_TOKEN=$MS_TOKEN
MS_STORE_SOURCE_ID=230e3774-5dbc-11ee-0a80-0eb900181194
MS_STORE_UZUM_ID=83cbdbb0-cbf4-11f1-0a80-177300049c5f
MS_AGENT_NAME=Uzum Market
DOC_STYLE=uzum-supply
SHOP_LABEL=ComFore shop
DB_PATH=$DIR/sync-comfore-shop.sqlite3
TRANSFERS_INITIAL_DAYS=14
ORDERS_INITIAL_DAYS=3
EOF
umask 022

say "5/6 Проверка без записи (init + dry-run)"
cd "$DIR"
.venv/bin/python main.py init 2>&1 | grep -v httpx
.venv/bin/python main.py sync-all --dry-run 2>&1 | grep -v -e httpx -e "HTTP 429"

if [ "${SKIP_SYSTEMD:-}" = 1 ]; then
  say "SKIP_SYSTEMD=1 — служба не устанавливается"; exit 0
fi
if [ "${ASSUME_YES:-}" != 1 ]; then
  ans="$(ask $'\nЗапустить синхронизацию как службу (пишет в МойСклад)? [y/N] ')"
  [[ "$ans" =~ ^[yYдД] ]] || { echo "Остановлено. Запустить позже: повторите установку."; exit 0; }
fi

say "6/6 Служба $SERVICE"
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Uzum ComFore shop (138842) -> MoySklad sync
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python main.py run
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q "$SERVICE"
systemctl restart "$SERVICE"
sleep 5
systemctl --no-pager --lines=15 status "$SERVICE" || true
echo
echo "Готово. Логи:      journalctl -u $SERVICE -f"
echo "Остановить:        systemctl stop $SERVICE"
echo "Обновить код:      повторите команду установки"
