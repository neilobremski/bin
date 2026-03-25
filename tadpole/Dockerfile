FROM alpine:3.20

RUN apk add --no-cache \
    bash \
    python3 \
    nodejs \
    npm \
    sqlite \
    coreutils \
    findutils \
    procps

# Install Claude CLI globally
RUN npm install -g @anthropic-ai/claude-code 2>/dev/null || true

# Copy the entire bin repo
COPY . /opt/bin

# Make everything writable for any UID
RUN chmod -R a+rwX /opt/bin && \
    chmod +x /opt/bin/stimulus \
              /opt/bin/circ-put /opt/bin/circ-get \
              /opt/bin/flock \
              /opt/bin/life/spark.sh /opt/bin/life/spark-loop.sh \
              /opt/bin/comms/live.sh \
              /opt/bin/brain/organs/pfc/live.sh \
              /opt/bin/tadpole/organs/hippocampus/live.sh

# Writable HOME for any UID (claude CLI needs it)
ENV HOME=/tmp/home
RUN mkdir -p /tmp/home && chmod a+rwX /tmp/home

# Create circ and locks directories
RUN mkdir -p /tmp/home/.life/circ /tmp/home/.life/locks && \
    chmod -R a+rwX /tmp/home/.life

ENV PATH="/opt/bin:$PATH"
WORKDIR /opt/bin/tadpole

ENTRYPOINT ["/opt/bin/life/spark-loop.sh", "/opt/bin/tadpole/life.conf"]
