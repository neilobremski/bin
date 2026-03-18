# Nervous System

The nervous system carries signals between organs via MQTT. Every body part runs a **ganglion** that routes messages to local organs.

## Two Kinds of Messages

**Direct signals** — addressed to a specific organ. The sender knows the target.

```
tadpole/tail → ganglion routes to organs/tail/stimulus.txt
```

**Emissions** — state broadcasts from an organ. No specific target. Captured by health.txt and the immune system, not routed as stimulus.

```
tadpole/heart → retained state, readable by anyone
```

The ganglion routes direct signals. Emissions are passive — they sit on the broker as retained messages.

## Configuration

```bash
# life.conf
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USER=myuser     # optional
MQTT_PASS=secret     # optional
```

## Publishing

Any organ can publish using `mqtt-pub`:

```bash
# Direct signal to another organ
mqtt-pub -t "organism/tail" -m "swim now"

# State emission (retained — latest wins)
mqtt-pub -t "organism/heart" -m "beat 42" -r

# Event from source (not retained — queued for persistent subscribers)
mqtt-pub -t "organism/stomach" -m "food circ:a1b2c3d4"
```

## The Ganglion

The ganglion drains MQTT and routes messages to local organs. It uses a **persistent session** so no messages are lost between cycles.

Each cycle:
1. Connect with stable client ID and persistent session
2. Drain all queued messages since last connection
3. Route: direct topics go to matching organ, source topics use routing table
4. Exit

The ganglion is the **only organ guaranteed to exist** on every body part (Layer 1).

## Routing

1. **Direct**: topic matches a local organ directory → route to its stimulus.txt
2. **Source-based**: topic is a source organ → routing table maps to target organ

Source-based routing lets organs publish without knowing the target. The ganglion decides where things go. This decouples organs from each other.

## Persistent Sessions

Retained messages are for **state** (latest wins — heartbeat, health).
Persistent sessions are for **events** (all queued — food produced, email arrived).

The broker queues event messages while the ganglion is disconnected. On reconnect, all queued messages are delivered. Nothing lost between cron cycles.

## No Broker, No Problem

If `MQTT_HOST` is unset, organs still function locally. Degradation, not failure.
