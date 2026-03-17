# Nervous System

The nervous system carries signals between organs via MQTT. It is configured through `life.conf` and consists of two parts: organs that **publish** signals and a **ganglion** organ that **routes** them.

## Configuration

```bash
# life.conf
MQTT_HOST=localhost
MQTT_PORT=1883
```

Organs inherit these as environment variables. If `MQTT_HOST` is unset, organs skip MQTT operations gracefully.

## Publishing

Any organ can publish a signal:

```bash
mosquitto_pub -h "$MQTT_HOST" -p "${MQTT_PORT:-1883}" \
  -t "organism/heartbeat" -m "beat 42" -r
```

The `-r` flag retains the message on the broker. Subscribers receive the last retained message immediately on connect — they don't have to be listening when the publish happens. Use retained messages for state ("I am alive") and non-retained for events ("something just happened").

## The Ganglion

The ganglion is an organ that bridges MQTT into `stimulus.txt` files. It is sparked on cadence like any other organ. Each cycle:

1. Subscribe to the organism's MQTT topics
2. Drain messages (short timeout — subscribe, collect, disconnect)
3. Append each message as a line in the target organ's `stimulus.txt`
4. Exit

The ganglion is not persistent. It runs, drains, routes, exits. The spark will spark it again next cycle.

```bash
# Drain with 2-second timeout, max 10 messages
messages=$(mosquitto_sub -h "$HOST" -p "$PORT" -t "organism/#" -W 2 -C 10)
echo "$messages" >> organs/tail/stimulus.txt
```

## Signal Chain

The full path from one organ to another:

```
heart (organ)
  → publishes "beat 42" to MQTT topic
  → broker holds retained message

ganglion (ganglion organ, sparked by cron)
  → subscribes to MQTT, drains messages
  → appends "beat 42" to tail/stimulus.txt

tail (organ, sparked by cron)
  → reads stimulus.txt, processes, empties file
```

This gives the organism both **periodic** (cron → spark → organ) and **event-driven** (MQTT → ganglion → stimulus → spark → organ) activation using the same infrastructure.

## No Broker, No Problem

If MQTT is not configured (`MQTT_HOST` unset), organs still function — they just can't send or receive signals through the nervous system. The heart still beats, it just doesn't propagate. This is degradation, not failure.
