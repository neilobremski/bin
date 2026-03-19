FROM alpine:3.20

RUN apk add --no-cache \
    bash \
    python3 \
    mosquitto-clients \
    sqlite \
    coreutils \
    findutils \
    procps

# The entire bin repo is COPYed so organs can find stimulus, mqtt-pub, etc.
COPY . /opt/bin
RUN chmod +x /opt/bin/stimulus /opt/bin/mqtt-pub /opt/bin/mqtt-sub \
    /opt/bin/flock /opt/bin/free-port /opt/bin/circ-put /opt/bin/circ-get \
    /opt/bin/local-secret \
    /opt/bin/life/spark.sh \
    /opt/bin/ganglion/live.sh \
    /opt/bin/tadpole/organs/*/live.sh

ENV PATH="/opt/bin:$PATH"
WORKDIR /opt/bin/tadpole
