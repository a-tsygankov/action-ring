# The vendored native transport (libtransport*.so) is linked against glibc,
# so this cannot use the Alpine-based Home Assistant base images.
FROM python:3.12-slim-bookworm

# Pinned upstream revision; bump deliberately after testing.
ARG STREAMDOCK_REF=67f670a0fc2175506321b9e6f42b89daab4dbc9f

ENV HOME=/data \
    XDG_CONFIG_HOME=/data/.config \
    XDG_CACHE_HOME=/data/.cache \
    XDG_STATE_HOME=/data/.local/state \
    PYTHONUNBUFFERED=1

COPY patch_ids.py /tmp/patch_ids.py

# gcc/libc6-dev are only needed to build python-evdev, then removed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libudev1 curl gcc libc6-dev \
    && pip install --no-cache-dir \
        "https://github.com/asad-albadi/streamdock-n3/archive/${STREAMDOCK_REF}.tar.gz" \
        websockets==15.0.1 \
    && python /tmp/patch_ids.py \
    && apt-get purge -y gcc libc6-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/patch_ids.py

COPY config.default.json /opt/streamdock/config.default.json
COPY ha-call /usr/local/bin/ha-call
COPY ha_daemon.py knob_accel.py /opt/streamdock/
COPY run.sh /run.sh
RUN chmod +x /usr/local/bin/ha-call /run.sh

CMD ["/run.sh"]
