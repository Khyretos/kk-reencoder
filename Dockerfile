# Dockerfile for kk-reencoder: AV1 re-encoding service using ffmpeg (BtbN static build with libvmaf and QSV)
# and the Python 3 standard library; the Intel media driver comes from Debian non-free.
FROM debian:trixie-slim

# Pinned ffmpeg: a BtbN month-end build (those are kept long-term), checked by sha256.
# To update: pick a newer month-end autobuild-YYYY-MM-DD release, take the linux64-gpl-8.1 asset and its sha256.
ARG FFMPEG_RELEASE=autobuild-2026-09-30-13-08
ARG FFMPEG_BUILD=ffmpeg-n8.1.3-9-g29e619e767-linux64-gpl-8.1
ARG FFMPEG_SHA256=97ce978979194b5cf7e06a5e68020dbdaa7a4f3294c5452b6a1bc347100dbb79

# Intel media driver + VPL runtime for QSV, tini, python3; BtbN's static ffmpeg 8.1 (libvmaf, libvpl, vaapi)
RUN sed -i 's/Components: main/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends \
    intel-media-va-driver-non-free libmfx-gen1.2 libvpl2 ca-certificates tini python3 curl xz-utils \
 && curl -fsSL -o /tmp/ffmpeg.tar.xz "https://github.com/BtbN/FFmpeg-Builds/releases/download/$FFMPEG_RELEASE/$FFMPEG_BUILD.tar.xz" \
 && echo "$FFMPEG_SHA256  /tmp/ffmpeg.tar.xz" | sha256sum -c - \
 && tar -xJf /tmp/ffmpeg.tar.xz -C /opt && rm /tmp/ffmpeg.tar.xz && mv "/opt/$FFMPEG_BUILD" /opt/ffmpeg \
 && ln -s /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg/bin/ffprobe /usr/local/bin/ \
 && apt-get purge -y curl xz-utils && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LIBVA_DRIVERS_PATH=/usr/lib/x86_64-linux-gnu/dri LIBVA_DRIVER_NAME=iHD \
    RX_DB=/data/reencoder.db

# RX_UID/RX_GID: the owner of the media files (the container runs as that user). The GPU's render group
# is added at run time with group_add (RX_RENDER_GID in docker-compose.override.example.yml), not baked in.
ARG RX_UID=1000
ARG RX_GID=1000
RUN (getent group "$RX_GID" >/dev/null || groupadd --gid "$RX_GID" reencoder) \
 && useradd --uid "$RX_UID" --gid "$RX_GID" --no-create-home --shell /usr/sbin/nologin reencoder \
 && mkdir -p /data && chown "$RX_UID:$RX_GID" /data

COPY app /app

USER reencoder
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

# the web server runs at normal priority so the page stays quick; every ffmpeg and ffprobe it starts runs at the
# lowest CPU and IO priority (nice 19, ionice idle) with a thread cap (RX_FFMPEG_THREADS)
ENTRYPOINT ["tini", "--"]
CMD ["python3", "/app/server.py"]
