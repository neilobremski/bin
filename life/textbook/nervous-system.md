# Nervous System

The nervous system carries signals between organs via MQTT. It is configured through `life.conf` and consists of two parts: organs that **publish** signals and a **ganglion** organ that **routes** them.

## Configuration

```bash
# life.conf
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USER=myuser     # optional
MQTT_PASS=secret     # optional
```

Organs inherit these as environment variables. If `MQTT_HOST` is unset, organs skip MQTT and function locally.

## Publishing

Any organ can publish a signal using `mqtt-pub` (a repo-root utility):

```bash
mqtt-pub -t "organism/tail" -m "beat 42" -r
```

The `-r` flag retains the message on the broker. Subscribers receive the last retained message immediately on connect. Use retained for state, non-retained for events.

The topic name is the address: `organism/<organ>` routes to that organ.

## The Ganglion

The ganglion is an organ that bridges MQTT into per-organ `stimulus.txt` files. It routes by topic name:

```
MQTT topic "organism/tail" → organs/tail/stimulus.txt
MQTT topic "organism/heart" → organs/heart/stimulus.txt
```

Each cycle:
1. Subscribe to `organism/#` with a short timeout
2. Drain messages, parse topic to get organ name
3. Append message to target organ's `stimulus.txt`
4. Exit

The ganglion is not persistent. It runs, drains, routes, exits.

Every body part runs its own ganglion. The ganglion only routes to organs that exist locally — unknown organ names are logged and dropped.

## Signal Chain

```
heart (periodic organ)
  → publishes "beat 42" to MQTT topic organism/tail

ganglion (periodic organ, next cron cycle)
  → subscribes to MQTT, drains messages
  → parses topic: organism/tail → organs/tail
  → appends "beat 42" to organs/tail/stimulus.txt

tail (dormant organ, next cron cycle)
  → spark sees stimulus.txt has content → launches
  → reads stimulus, processes, empties file
```

This gives the organism both **periodic** (cadence) and **event-driven** (stimulus) activation using the same spark.

## No Broker, No Problem

If `MQTT_HOST` is unset, organs still function — they just can't send or receive signals through the nervous system. Degradation, not failure.
