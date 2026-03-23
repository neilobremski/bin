FROM alpine:3.20

RUN apk add --no-cache \
    bash \
    python3 \
    py3-pip \
    mosquitto-clients \
    sqlite \
    curl \
    coreutils \
    findutils \
    procps \
    ca-certificates \
    nodejs \
    npm

# Install claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Copy organism tools (brain-safe only — no gmail, gas, transcribe)
COPY life/ /opt/life/
COPY ganglion/ /opt/ganglion/
COPY muscles.py /opt/
COPY stimulus /opt/
COPY circ-put /opt/
COPY circ-get /opt/
COPY memories /opt/
COPY small-llm /opt/
COPY local-secret /opt/
COPY mqtt-pub /opt/
COPY mqtt-sub /opt/
COPY flock /opt/

# Copy brain body part
COPY brain/ /brain/

# Copy hippocampus (shared organ — source lives in tadpole, copied into brain container)
COPY tadpole/organs/hippocampus/ /brain/organs/hippocampus/

RUN chmod +x /opt/stimulus /opt/mqtt-pub /opt/mqtt-sub \
    /opt/flock /opt/circ-put /opt/circ-get /opt/local-secret \
    /opt/life/spark.sh /opt/life/spark-loop.sh /opt/life/spark-organ.sh \
    /opt/ganglion/live.sh \
    /brain/organs/pfc/live.sh

ENV PATH="/opt:/opt/life:$PATH"
ENV PYTHONPATH="/opt"
ENV HOME="/brain"
ENV CONF_DIR="/brain"

WORKDIR /brain

# Create .claude directory for session persistence
RUN mkdir -p /brain/.claude /brain/.life/locks /brain/.life/circ

# Persistent volumes (mounted at runtime, not baked in)

ENTRYPOINT ["bash", "/opt/life/spark-loop.sh", "/brain/life.conf"]
