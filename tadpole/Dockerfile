FROM alpine:3.20

RUN apk add --no-cache \
    bash python3 mosquitto-clients sqlite curl \
    coreutils findutils procps ca-certificates nodejs npm

# Install claude CLI (for PFC brain organ)
RUN npm install -g @anthropic-ai/claude-code

# Copy entire bin repo — tadpole is a test organism, no isolation
COPY . /opt/bin

RUN chmod +x /opt/bin/stimulus /opt/bin/mqtt-pub /opt/bin/mqtt-sub \
    /opt/bin/flock /opt/bin/circ-put /opt/bin/circ-get \
    /opt/bin/local-secret /opt/bin/gmail /opt/bin/transcribe \
    /opt/bin/life/spark.sh /opt/bin/life/spark-loop.sh /opt/bin/life/spark-organ.sh \
    /opt/bin/comms/live.sh /opt/bin/brain/organs/pfc/live.sh \
    /opt/bin/tadpole/organs/*/live.sh 2>/dev/null || true

ENV PATH="/opt/bin:/opt/bin/life:$PATH"
ENV PYTHONPATH="/opt/bin"

# Non-root user (claude CLI refuses --dangerously-skip-permissions as root)
RUN adduser -D -h /tadpole tadpole
RUN mkdir -p /tadpole/.claude /tadpole/.life/locks /tadpole/.life/circ \
    && chown -R tadpole:tadpole /tadpole /opt/bin

USER tadpole
ENV HOME="/tadpole"
WORKDIR /opt/bin
