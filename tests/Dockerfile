FROM alpine:3.21
RUN apk add --no-cache bash coreutils grep sed procps
# Copy spark code
COPY life/ /opt/bin/life/
COPY install.sh /opt/bin/install.sh
COPY tests/ /tests/
WORKDIR /tests
ENTRYPOINT ["bash", "test-spark.sh"]
