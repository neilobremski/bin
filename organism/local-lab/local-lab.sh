#!/bin/bash
cd "$(dirname "$0")"
export ORGANS="./organs/ping:./organs/pong"
export PATH="$(pwd)/bin:$PATH"

# remove temporary files
rm -f organs/ping/.lock
rm -f organs/ping/.ticks
rm -f organs/pong/.lock
rm -f organs/pong/.ticks
rm -rf organs/ping/.stimulus
rm -rf organs/pong/.stimulus
rm -rf .circulatory

echo "=== Local Lab: ping/pong demo ==="
spark-cron # .ticks = 0 -> increments to 1
spark-cron # .ticks = 1 >= cadence 1 -> fires both organs
wait
echo "=== Done ==="
