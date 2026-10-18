# kk-reencoder

A self-hosted service that re-encodes a video library to AV1 only where a VMAF search proves the result keeps the quality (VMAF >= 95 by default) at a clearly smaller size. Designed for users running their own media server (Jellyfin/Plex) on their own machine.

## How it works

The service scans library folders in alphabetical order. It identifies series and movies by title folder; season folders are counted as part of their series.

It skips files that are already AV1, HDR (by default), low-bitrate, or use unselected codecs.

For each file:
1. It cuts 3 x 20 s samples.
2. It makes a lossless reference.
3. It encodes them at several quality levels.
4. It scores them with VMAF.
5. It picks the smallest encoding that reaches the target and is at most 80% of the original size.

After searching 2 files per series, the service reuses that quality setting with one check sample for the rest of the series.

## Modes

### Report Mode (`RX_MODE=report`)
Measures and estimates only. Media can be mounted read-only. No files are modified.

### Stage Mode (`RX_MODE=stage`)
Encodes the whole file next to the original into a hidden `.reencoder` folder as `[reencoded] <name>`. It creates 5 comparison frames (two at the worst-scoring moments). Nothing is replaced until the user presses Confirm.

**Confirm:** Swaps the original kept as `[original] <name>` in the same hidden folder.
**Undo:** Swaps back to the staged copy.
**Finalize:** Deletes the kept original and is the only step that frees space.
**Discard:** Drops the staged copy.

Jellyfin ignores hidden folders.

## Reports

Every folder gets `encoding_report.json`. This file is stored in the data volume and also in the media folder after finalize. It contains per-file before/after size, VMAF, quality, and status.

## Web Interface

- **Library View:** Shows per-series status (done, partly done with what is left, ready to confirm, not started). Includes pause, priority (Up next), and batch encoding (1-5 encodes at once). Features a before/after slider.
- **Overview Page:** Charts and live CPU/memory usage.

## Encoders

- **qsv:** Intel Arc/iGPU (default).
- **vaapi:** Intel iGPU.
- **nvenc:** NVIDIA.
- **svt:** CPU only (slow).

Optional deband filter per library via `RX_FILTERS` (default: Anime and Cartoons). VMAF is then measured against the debanded picture.

## Settings

| Variable | Default | Description |
| :--- | :--- | :--- |
| `RX_ROOTS` | `/media/*` | Comma list of library folders (required if not using default). |
| `RX_MODE` | `report` | `report` or `stage`. |
| `RX_ENCODER` | `qsv` | `qsv`, `vaapi`, `nvenc`, `svt`. |
| `RX_DEVICE` | `/dev/dri/renderD128` | GPU device path. |
| `RX_TARGET_VMAF` | `95` | Target VMAF score. |
| `RX_MAX_RATIO` | `0.8` | Max output size ratio (0.8 = 80%). |
| `RX_PARALLEL` | `2` | Number of concurrent encodes (1-5). |
| `RX_FILTERS` | `Anime=deband,Cartoons=deband` | Debanding rules per library. |
| `RX_MAX_STAGED_GB` | `1000` | Pause staging if total exceeds this. |
| `RX_VMAF_THREADS` | `4` | Threads for VMAF analysis. |
| `RX_SAMPLES` | `3` | Number of samples per file. |
| `RX_SAMPLE_LEN` | `20` | Length of each sample in seconds. |
| `RX_SKIP_HDR` | `true` | Skip HDR files. |
| `RX_SEARCH_PER_FOLDER` | `2` | Files to search per series before locking quality. |
| `RX_PORT` | `8080` | Web UI port. |

## Quick Start

Create a `docker-compose.yml`:

```yaml
version: "3"
services:
  reencoder:
    build: .
    container_name: kk-reencoder
    user: "1000:1000"
    group_add:
      - "render"
    devices:
      - /dev/dri:/dev/dri
    volumes:
      - /path/to/media:/media:ro
      - /path/to/data:/data
    environment:
      - RX_ROOTS=/media
      - RX_MODE=report
      - RX_PORT=8080
    mem_limit: 3g
    cpus: "4"
    ports:
      - "127.0.0.1:8080:8080"
```

Notes:
- Find your render group ID with `getent group render`.
- Mount media as `:ro` for report mode, read-write for stage mode.
- Put the service behind a sign-in page; it has no login of its own.

## Safety

- Runs at the lowest CPU and IO priority.
- One GPU shared with other apps is fine. Lower `RX_PARALLEL` or press Pause when the GPU is needed.

## Development

Run tests:
```bash
python3 -m unittest discover tests
```

Run UI check:
```bash
tests/ui/run-check.sh
```

## Licence

ffmpeg is the BtbN static GPL build. Apache ECharts 5.5.1 is bundled (Apache-2.0, see `ECHARTS-LICENSE.txt`).
