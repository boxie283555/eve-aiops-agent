set -eu

toml_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

cp /etc/telegraf/telegraf.base.conf /tmp/telegraf.conf

if [ "${TELEGRAF_ENABLE_GNMI:-false}" = "true" ]; then
  gnmi_addresses="${TELEGRAF_GNMI_ADDRESSES:-}"
  if [ -z "$gnmi_addresses" ]; then
    echo "TELEGRAF_ENABLE_GNMI=true but TELEGRAF_GNMI_ADDRESSES is empty" >&2
    exit 1
  fi

  toml_addresses=""
  old_ifs="$IFS"
  IFS=","
  for address in $gnmi_addresses; do
    escaped_address="$(toml_escape "$address")"
    if [ -n "$toml_addresses" ]; then
      toml_addresses="$toml_addresses, "
    fi
    toml_addresses="$toml_addresses\"$escaped_address\""
  done
  IFS="$old_ifs"

  gnmi_username="$(toml_escape "${AIOPS_DEVICE_USERNAME:-}")"
  gnmi_password="$(toml_escape "${AIOPS_DEVICE_PASSWORD:-}")"

  cat >>/tmp/telegraf.conf <<EOF

[[inputs.gnmi]]
  addresses = [$toml_addresses]
  username = "$gnmi_username"
  password = "$gnmi_password"
  encoding = "json_ietf"
  tls_enable = false
  redial = "10s"

  [[inputs.gnmi.subscription]]
    name = "arista_interface_counters"
    path = "/interfaces/interface/state/counters"
    subscription_mode = "sample"
    sample_interval = "30s"

  [[inputs.gnmi.subscription]]
    name = "arista_bgp_neighbors"
    path = "/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state"
    subscription_mode = "sample"
    sample_interval = "30s"
EOF
fi

exec telegraf --config /tmp/telegraf.conf
