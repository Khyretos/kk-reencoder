<p align="center">
  <img src="docs/branding/banner.png" alt="Kreative Reencoder: same picture, less space" width="100%">
</p>

# Kreative Reencoder

A self-hosted service that re-encodes a video library to AV1 only where a VMAF search proves the result keeps the quality (VMAF >= 95 by default) at a clearly smaller size. Nothing is replaced until you compare and confirm.

Main repository: [git.kreative-kompas.com/khyretos/kk-reencoder](https://git.kreative-kompas.com/khyretos/kk-reencoder), mirrored to [github.com/Khyretos/kk-reencoder](https://github.com/Khyretos/kk-reencoder).

## Screenshots

| | |
|---|---|
| ![Library](docs/screenshots/01-library.png) | ![Series](docs/screenshots/02-series.png) |
| Library: totals, space per library and what is encoding now | Every series with its status, saving and what is left |
| ![Compare](docs/screenshots/03-compare.png) | ![Overview](docs/screenshots/04-overview.png) |
| Compare original and AV1 frames: divider, zoom, hold to flip | Overview: where the space went, savings over time, encoder load |
| <img src="docs/screenshots/05-light.png" alt="Light theme" width="45%"> | <img src="docs/screenshots/06-phone.png" alt="On a phone" width="45%"> |
| Light theme | On a phone |

The screenshots come from a real library. `docs/screenshots/shoot.mjs` takes them again from a running instance.

## Features

- **AV1 on Intel QSV** with full-GPU path (decode, 10-bit conversion, encode).
- **Hardware acceleration** via VAAPI, NVENC or CPU SVT-AV1.
- **VMAF-proven quality** per file; nothing replaces the original without confirmation.
- **Modes**: report (measure only), manual (tick series to encode), stage (encode next to original).
- **Batch control**: pause, priorities, 1-5 encodes at once.
- **Queue**: see which series are encoded or measured next; drag to reorder (mouse or finger), send one to the front, take it out and put it back.
- **Settings screen**: mode (report, manual, automatic), encodes at once, daily rescan, quality target and size limit, without editing the compose file.
- **Staged copies** in a hidden `.reencoder` folder; Compare, Confirm, Undo, Finalize steps.
- **Safety**: originals are never deleted before Finalize; space guard (`RX_MAX_STAGED_GB`).
- **Reports**: `encoding_report.json` per series; overview with charts; live CPU/memory per job.
- **Scans** in the background (daily, per library or per series); works on phones, dark and light themes.
- **Glance or detail**: every panel folds to a one-line summary of its numbers; cards and rows are coloured by state (done, ready to confirm, partly done, queued, running, skipped).

## How it works

The service scans library folders in alphabetical order. It identifies series and movies by title folder; season folders are counted as part of their series.

It skips files that are already AV1, HDR (by default; "Skip HDR videos" on the Settings screen), low-bitrate, or use unselected codecs. With HDR allowed, HDR10 and HLG files stay HDR (colour tags and mastering metadata kept); Dolby Vision drops to its HDR10 picture, and Dolby Vision profile 5 (no HDR10 picture) is always skipped. Every skipped or failed file shows why in the series window, with the measured numbers when the quality search decided (for example "at VMAF 95 the AV1 copy would be 97% of the original size; the size limit is 95%"). Files skipped because of a setting are looked at again by the next scan, and changing the quality target or size limit measures the "too big" ones again.

For each file:
1. It cuts 3 x 20 s samples.
2. It makes a lossless reference.
3. It encodes them at several quality levels.
4. It scores them with VMAF.
5. It picks the smallest encoding that reaches the target and is at most 95% of the original size (any saving of 5% or more counts; up to 99% on the Settings screen).

After searching 2 files per series, the service reuses that quality setting with one check sample for the rest of the series.

## Deploy

You need Docker with Compose v2.24 or newer. A GPU is optional (Intel Arc/iGPU for `qsv`, Intel/AMD for `vaapi`); without one, use `RX_ENCODER=svt` (CPU, slow) or just measure in report mode.

1. Clone this repo and enter it.
2. `cp .env.example .env` and set at least:
   - `RX_MEDIA_DIR`: the host folder with your library (it is mounted as `/media`), and `RX_ROOTS` if only some folders in it should be scanned, e.g. `/media/Anime,/media/Series`.
   - `RX_UID` and `RX_GID`: the owner of the media files (`id -u`, `id -g`). The container runs as that user; re-encodes go into hidden `.reencoder` folders next to the originals, so it needs write access in manual and stage mode.
   - `RX_MODE`: `report` (default, measure only, changes nothing), `manual` or `stage`.
   - `TZ`, e.g. `Europe/Amsterdam`.
3. For a GPU or a reverse proxy: `cp docker-compose.override.example.yml docker-compose.override.yml`. For the GPU set `RX_RENDER_DEVICE` (`ls /dev/dri`) and `RX_RENDER_GID` (`getent group render | cut -d: -f3`) in `.env`. For a proxy on a Docker network set `RX_PROXY_NETWORK`; the override then publishes no host port. Delete the part you do not need (the proxy part includes the `ports: !reset []` line).
4. `docker compose up -d --build`, then open http://127.0.0.1:8080 (or your proxy host). The first scan runs in the background.

It has no login of its own: put it behind a sign-in before you open it to the network.

Update: `git pull && docker compose up -d --build`. Remove: `docker compose down` (add `-v` to also delete the database and reports; your media and any `.reencoder` folders stay).

The image pins ffmpeg to a BtbN month-end build checked by sha256 (`FFMPEG_*` build args in the Dockerfile); to update, take a newer month-end `autobuild-*` release's `linux64-gpl-8.1` asset and its sha256.

## Settings

All of these go in `.env`; `.env.example` lists them with the host settings (`RX_MEDIA_DIR`, `RX_UID`, `RX_GID`, `RX_RENDER_DEVICE`, `RX_RENDER_GID`, `RX_PROXY_NETWORK`, `RX_BIND`, `RX_HOST_PORT`, `RX_IMAGE`, `RX_MEM_LIMIT`, `RX_CPUS`, `TZ`). `RX_DEVICE` is set from `RX_RENDER_DEVICE`.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `RX_ROOTS` | every folder in `/media` | Comma list of library folders inside the container. |
| `RX_MODE` | `report` | `report` (measure only), `manual` (only series ticked on the page are encoded) or `stage` (encode everything that qualifies). |
| `RX_ENCODER` | `qsv` | `qsv`, `vaapi`, `nvenc`, `svt`. |
| `RX_DEVICE` | `/dev/dri/renderD128` | GPU device path. |
| `RX_TARGET_VMAF` | `95` | Target VMAF score. |
| `RX_MAX_RATIO` | `0.95` | Max output size ratio (0.95 = keep anything that saves 5% or more; up to 0.99). |
| `RX_PARALLEL` | `2` | Number of concurrent encodes (1-5). |
| `RX_HW_DECODE` | `true` | QSV only: decode and 10-bit conversion on the GPU too (about 5x faster); falls back to CPU decode per file. |
| `RX_FILTERS` | (none) | Optional CPU filters per library, e.g. `Anime=deband`. |
| `RX_MAX_STAGED_GB` | `1000` | Pause staging if total exceeds this. |
| `RX_VMAF_THREADS` | `4` | Threads for VMAF analysis. |
| `RX_FFMPEG_THREADS` | `2` | Threads per ffmpeg decoder, encoder and filter graph. ffmpeg otherwise starts one per host CPU, which overloads a CPU-limited container and can run it out of memory. |
| `RX_SAMPLES` | `3` | Number of samples per file. |
| `RX_SAMPLE_LEN` | `20` | Length of each sample in seconds. |
| `RX_SKIP_HDR` | `true` | Skip HDR files. Also on the Settings screen. Off: HDR10/HLG stay HDR, Dolby Vision keeps its HDR10 picture. |
| `RX_CODECS` | `h264,hevc,mpeg4,vc1,mpeg2video,wmv3,msmpeg4v3` | Source codecs to consider (AV1 is always skipped). |
| `RX_MAX_HEIGHT` | `2160` | Skip taller videos. |
| `RX_RESCAN_S` | `86400` | Rescan the library every N seconds (once a day), in the background. Also on the Settings screen. |
| `RX_CHECK_EVERY_EPISODE` | `true` | `false`: once a series has its quality, later episodes skip the CPU check sample. Also on the Settings screen. |
| `RX_SEARCH_PER_FOLDER` | `2` | Files to search per series before locking quality. |

## Safety

- Originals are never replaced without Confirm.
- Finalize is the only delete.
- The space guard (`RX_MAX_STAGED_GB`) pauses staging if limits are exceeded.
- Every ffmpeg runs at the lowest CPU/IO priority; the web page itself does not, so it stays responsive.
- One job per GPU engine is fine.

## Development

Run tests:
```bash
python3 -m unittest discover tests
```

Run UI check:
```bash
tests/ui/run-check.sh
```

Run benchmarks:
```bash
docs/benchmarks-abtest.sh
```

Tests include `tests/ui/check.mjs` (browser check) and `tests/ui/phone.mjs` (phone-speed check against a running instance). `docs/benchmarks-abtest.sh` measures quality/size per quality level on sample files.

## Credits and licences

ffmpeg is the BtbN static GPL build, with libvmaf by Netflix. Apache ECharts 5.5.1 is bundled (Apache-2.0, see `app/static/ECHARTS-LICENSE.txt`). The VMAF-search idea comes from ab-av1. Project licence: see LICENSE.
