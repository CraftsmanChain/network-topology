#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${1:-slxz-zy}"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "缺少依赖: ${cmd}" >&2
    exit 1
  fi
}

query_count() {
  local base_url="$1"
  local token="$2"
  local expr="$3"
  local body_file http_code query_url count_value
  local -a curl_args

  body_file="$(mktemp)"
  query_url="${base_url%/}/api/v1/query"
  curl_args=(
    -ksS
    --compressed
    -o "${body_file}"
    -w "%{http_code}"
    -H "Accept-Encoding: gzip"
    --get
    --data-urlencode "query=count(${expr})"
    "${query_url}"
  )

  if [[ -n "${token}" ]]; then
    curl_args+=(-H "Authorization: Bearer ${token}")
  fi

  http_code="$(curl "${curl_args[@]}")"

  if [[ "${http_code}" != "200" ]]; then
    rm -f "${body_file}"
    echo "HTTP ${http_code}"
    return 1
  fi

  count_value="$(jq -r '.data.result[0].value[1] // "0"' "${body_file}" 2>/dev/null || echo "__JQ_ERROR__")"
  rm -f "${body_file}"

  if [[ "${count_value}" == "__JQ_ERROR__" ]]; then
    echo "invalid json"
    return 1
  fi

  awk "BEGIN { printf \"%d\", (${count_value} + 0) }"
}

run_stage() {
  local base_url="$1"
  local token="$2"
  local stage="$3"
  local metrics_text="$4"
  local expr count output

  echo "[${stage}]"
  while IFS= read -r expr; do
    [[ -z "${expr}" ]] && continue
    if output="$(query_count "${base_url}" "${token}" "${expr}" 2>&1)"; then
      count="${output}"
      if [[ "${count}" -gt 0 ]]; then
        echo "  - ${expr}: OK (count=${count})"
      else
        echo "  - ${expr}: MISSING (count=0)"
        MISSING_METRICS+=("${expr}")
      fi
    else
      echo "  - ${expr}: MISSING (error=${output})"
      FAILED_METRICS+=("${expr}")
    fi
  done <<< "${metrics_text}"
  echo
}

run_check() {
  local name="$1"
  local source_type="$2"
  local cluster="$3"
  local base_url="$4"
  local token="${5:-}"
  local devices_metrics topology_metrics lldp_metrics

  echo "=== ${cluster} (${source_type}) ==="
  echo "=== ${name} ==="

  MISSING_METRICS=()
  FAILED_METRICS=()

  devices_metrics=$'up\nup{job=~"snmp.*"}\nsysName'
  topology_metrics=$'ifHighSpeed\nifOperStatus\nifHCInOctets\nifHCOutOctets\nifAlias'
  lldp_metrics=$'lldpLocPortId\nlldpRemSysName\nlldpRemPortId'

  run_stage "${base_url}" "${token}" "devices" "${devices_metrics}"
  run_stage "${base_url}" "${token}" "topology" "${topology_metrics}"
  run_stage "${base_url}" "${token}" "lldp" "${lldp_metrics}"

  if [[ "${#MISSING_METRICS[@]}" -gt 0 || "${#FAILED_METRICS[@]}" -gt 0 ]]; then
    echo "缺失指标:"
    if [[ "${#MISSING_METRICS[@]}" -gt 0 ]]; then
      for expr in "${MISSING_METRICS[@]}"; do
        echo "  - ${expr}"
      done
    fi
    if [[ "${#FAILED_METRICS[@]}" -gt 0 ]]; then
      echo "查询失败:"
      for expr in "${FAILED_METRICS[@]}"; do
        echo "  - ${expr}"
      done
    fi
  else
    echo "关键指标完整"
  fi
}

require_cmd curl
require_cmd jq

case "${ENV_NAME}" in
  slxz-ali)
    run_check \
      "slxz-ali-query-gateway" \
      "vm" \
      "slxz-ali" \
      "https://10.255.171.88:17090/api/v1/query-gateway/prometheus/datasources/ab483f2d-a128-46c6-bd52-c72b890d9bec" \
      "mds_XK2UEol0MYgP6PyIvWS5mTGBPozi6b8UJPGyfnDhAxc"
    ;;
  slxz-zy)
    run_check \
      "slxz-zy-query-gateway" \
      "vm" \
      "slxz-zy" \
      "https://10.255.171.88:17090/api/v1/query-gateway/prometheus/datasources/f5e1f567-2044-4084-af27-f598de1d907c" \
      "mds_Nuawpzn_gF2YXoHy4VV9tyGChkeWRyuKH7_-phoRS00"
    ;;
  zwzp)
    run_check \
      "zwzp-query-gateway" \
      "vm" \
      "zwzp" \
      "https://10.255.171.88:17090/api/v1/query-gateway/prometheus/datasources/7acb5eec-f778-4edf-a7ed-1a036bf67e43" \
      "mds_X7nFsnWeJHEwq7dwOWMmyXoQEwMXVvEjRmw5vlEKER0"
    ;;
  yczy)
    run_check \
      "yczy-query-gateway" \
      "prometheus" \
      "yczy" \
      "https://10.255.171.88:17090/api/v1/query-gateway/prometheus/datasources/a414586b-5e43-41f2-adb4-13ac7a143711" \
      "mds_jDoANCAPE2RBE0XvS_4xNpJO83cJob70bps35E-bf4Y"
    ;;
  all)
    run_check \
      "slxz-ali-query-gateway" \
      "vm" \
      "slxz-ali" \
      "https://10.255.171.88:17090/api/v1/query-gateway/prometheus/datasources/ab483f2d-a128-46c6-bd52-c72b890d9bec" \
      "mds_XK2UEol0MYgP6PyIvWS5mTGBPozi6b8UJPGyfnDhAxc"
    echo
    run_check \
      "slxz-zy-query-gateway" \
      "vm" \
      "slxz-zy" \
      "https://10.255.171.88:17090/api/v1/query-gateway/prometheus/datasources/f5e1f567-2044-4084-af27-f598de1d907c" \
      "mds_Nuawpzn_gF2YXoHy4VV9tyGChkeWRyuKH7_-phoRS00"
    echo
    run_check \
      "zwzp-query-gateway" \
      "vm" \
      "zwzp" \
      "https://10.255.171.88:17090/api/v1/query-gateway/prometheus/datasources/7acb5eec-f778-4edf-a7ed-1a036bf67e43" \
      "mds_X7nFsnWeJHEwq7dwOWMmyXoQEwMXVvEjRmw5vlEKER0"
    echo
    run_check \
      "yczy-query-gateway" \
      "prometheus" \
      "yczy" \
      "https://10.255.171.88:17090/api/v1/query-gateway/prometheus/datasources/a414586b-5e43-41f2-adb4-13ac7a143711" \
      "mds_jDoANCAPE2RBE0XvS_4xNpJO83cJob70bps35E-bf4Y"
    ;;
  *)
    echo "用法: bash cmd/test_query_gateway_metrics.sh [slxz-ali|slxz-zy|zwzp|yczy|all]" >&2
    exit 1
    ;;
esac
