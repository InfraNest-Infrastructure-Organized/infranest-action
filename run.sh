#!/bin/sh
# The InfraNest GitHub Action: one command per step, against the InfraNest API. See README.md.
set -eu

: "${INFRANEST_TOKEN:?The token input is required}"
: "${INFRANEST_ORG:?The organization input is required}"
API="${INFRANEST_API:-https://api.infranest.io/api}"
ORG="/organizations/$INFRANEST_ORG"
# Never print the token, even if a later step echoes the environment.
echo "::add-mask::$INFRANEST_TOKEN"

# infranest METHOD PATH [JSON BODY] [IDEMPOTENCY KEY]
infranest() {
  if [ -n "${3:-}" ]; then
    curl -sS --fail-with-body -X "$1" "$API$2" \
      -H "Authorization: Bearer $INFRANEST_TOKEN" -H "Accept: application/json" \
      -H "Content-Type: application/json" -H "User-Agent: infranest-action" ${4:+-H "Idempotency-Key: $4"} -d "$3"
  else
    curl -sS --fail-with-body -X "$1" "$API$2" \
      -H "Authorization: Bearer $INFRANEST_TOKEN" -H "Accept: application/json" -H "User-Agent: infranest-action"
  fi
}

ids() { # "1, 2,3" → [1,2,3]
  jq -cn --arg list "${1:-}" '$list | split(",") | map(gsub("\\s"; "")) | map(select(length > 0) | tonumber)'
}

in_minutes() {
  date -u -d "+$1 minutes" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v "+$1M" +%Y-%m-%dT%H:%M:%SZ
}

case "${COMMAND:-}" in
  maintenance-open)
    if [ -z "${CHECK_IDS:-}" ] && [ -z "${SERVER_IDS:-}" ]; then
      echo "::error::maintenance-open needs check-ids or server-ids" >&2
      exit 1
    fi
    # A list that is not given is left out, not sent empty: a window with no monitors covers every monitor.
    body=$(jq -n --arg name "${NAME:-Deploy}" --arg start "$(in_minutes 0)" --arg end "$(in_minutes "${MINUTES:-30}")" \
      --argjson checks "$(ids "${CHECK_IDS:-}")" --argjson servers "$(ids "${SERVER_IDS:-}")" \
      '{name: $name, starts_at: $start, ends_at: $end, suppress_alerts: true}
       + (if ($checks | length) > 0 then {check_ids: $checks} else {} end)
       + (if ($servers | length) > 0 then {server_ids: $servers} else {} end)')
    id=$(infranest POST "$ORG/maintenance-windows" "$body" "${IDEMPOTENCY_KEY:-}" | jq -r .id)
    echo "window-id=$id" >> "${GITHUB_OUTPUT:-/dev/null}"
    echo "Opened maintenance window $id."
    ;;
  maintenance-close)
    : "${WINDOW_ID:?maintenance-close needs window-id}"
    infranest PATCH "$ORG/maintenance-windows/$WINDOW_ID/complete" > /dev/null
    echo "Closed maintenance window $WINDOW_ID; alerts are back on."
    ;;
  monitor-pause)
    : "${MONITOR_ID:?monitor-pause needs monitor-id}"
    infranest POST "$ORG/monitoring-checks/$MONITOR_ID/pause" > /dev/null
    echo "Paused monitor $MONITOR_ID."
    ;;
  monitor-resume)
    : "${MONITOR_ID:?monitor-resume needs monitor-id}"
    infranest POST "$ORG/monitoring-checks/$MONITOR_ID/resume" > /dev/null
    echo "Resumed monitor $MONITOR_ID."
    ;;
  sandbox-reset)
    infranest POST "$ORG/sandbox/reset" > /dev/null
    echo "The sandbox has its sample data back."
    ;;
  *)
    echo "::error::Unknown command '${COMMAND:-}'. Use maintenance-open, maintenance-close, monitor-pause, monitor-resume or sandbox-reset." >&2
    exit 1
    ;;
esac
