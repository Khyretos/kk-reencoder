# Dockerfile for kk-reencoder: AV1 re-encoding service using ffmpeg (BtbN static build with libvmaf and QSV)
# and the Python 3 standard library; the Intel media driver comes from Debian non-free.
FROM debian:trixie-slim

# Intel media driver + VPL runtime for QSV, tini, python3; BtbN's static ffmpeg 8.1 (libvmaf, libvpl, vaapi)
RUN sed -i 's/Components: main/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends \
    intel-media-va-driver-non-free libmfx-gen1.2 libvpl2 ca-certificates tini python3 curl xz-utils \
 && curl -fsSL https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz | tar -xJ -C /opt \
 && ln -s /opt/ffmpeg-n8.1-latest-linux64-gpl-8.1/bin/ffmpeg /opt/ffmpeg-n8.1-latest-linux64-gpl-8.1/bin/ffprobe /usr/local/bin/ \
 && apt-get purge -y curl xz-utils && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LIBVA_DRIVERS_PATH=/usr/lib/x86_64-linux-gnu/dri LIBVA_DRIVER_NAME=iHD \
    RX_DB=/data/reencoder.db

# uid/gid 1000 owns the media files; gid 993 is the host's render group (/dev/dri/renderD*)
RUN groupadd --gid 1000 reencoder && groupadd --gid 993 render \
 && useradd --uid 1000 --gid 1000 --groups render --no-create-home --shell /usr/sbin/nologin reencoder \
 && mkdir -p /data && chown reencoder:reencoder /data

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
