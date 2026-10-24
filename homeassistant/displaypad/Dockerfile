FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libusb-1.0-0 \
    && pip install --no-cache-dir pyusb==1.3.1 pillow==11.3.0 \
    && rm -rf /var/lib/apt/lists/*

COPY config.default.json displaypad.py /opt/displaypad/
COPY icons /opt/displaypad/icons
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
