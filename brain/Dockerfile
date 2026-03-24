FROM alpine:3.20

RUN apk add --no-cache \
    bash \
    python3 \
    mosquitto-clients \
    sqlite \
    coreutils \
    findutils \
    procps \
    nodejs \
    npm

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Copy the entire bin repo so organs can find stimulus, mqtt-pub, etc.
COPY . /opt/bin

# Make all scripts executable and everything world-accessible (any UID)
RUN chmod +x /opt/bin/stimulus /opt/bin/mqtt-pub /opt/bin/mqtt-sub \
    /opt/bin/flock /opt/bin/free-port /opt/bin/circ-put /opt/bin/circ-get \
    /opt/bin/local-secret \
    /opt/bin/life/spark.sh /opt/bin/life/spark-loop.sh /opt/bin/life/spark-organ.sh \
    /opt/bin/ganglion/live.sh \
    && chmod -R a+rwX /opt/bin

# Writable HOME for any UID
ENV HOME=/tmp/home
RUN mkdir -p /tmp/home/.life/locks && chmod -R 777 /tmp/home

ENV PATH="/opt/bin:$PATH"
WORKDIR /opt/bin/brain
ENTRYPOINT ["/opt/bin/life/spark-loop.sh"]
