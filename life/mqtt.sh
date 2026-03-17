#!/usr/bin/env bash
# mqtt.sh — MQTT helper. Source this to get mqtt_pub and mqtt_sub functions.
# Reads MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS from environment (set by life.conf).

_mqtt_args() {
  local args="-h ${MQTT_HOST:-localhost} -p ${MQTT_PORT:-1883}"
  [ -n "${MQTT_USER:-}" ] && args="$args -u $MQTT_USER"
  [ -n "${MQTT_PASS:-}" ] && args="$args -P $MQTT_PASS"
  [ "${MQTT_PORT:-1883}" = "8883" ] && args="$args --capath /etc/ssl/certs"
  echo "$args"
}

mqtt_pub() { mosquitto_pub $(_mqtt_args) "$@"; }
mqtt_sub() { mosquitto_sub $(_mqtt_args) "$@"; }
