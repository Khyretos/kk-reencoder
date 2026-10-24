# Changelog

## Unreleased

- Deploy with `docker-compose.yml` and `.env` only: the compose file works on its own (report mode on ./media, port 127.0.0.1:8080), every setting and host path is an env var with a default (`.env.example`), and GPU and proxy network go in `docker-compose.override.example.yml`. The media owner (`RX_UID`/`RX_GID`) is a build arg and the render group is added with `group_add` (`RX_RENDER_GID`) instead of a hardcoded 993. ffmpeg is pinned to BtbN's 2026-09-30 month-end build (8.1.3) and checked by sha256. `docker-compose.example.yml` is gone.
- Skips explained: every skipped or failed file shows why under its status (also on phones), with the measured numbers when the quality search decided ("at VMAF 95 the AV1 copy would be 93% of the original size; the size limit is 90%"). Failures keep the real ffmpeg error instead of libva start-up lines; a killed ffmpeg says it most likely ran out of memory.
- Any real saving counts: size limit default 95% (was 80%), up to 99% on the Settings screen. The full-file check no longer throws away an encode whose samples passed: the whole file only has to come out smaller (audio is copied, so it shrinks by less than the video). Changing the target or size limit measures the files skipped as "too big" again; files skipped because of a setting (HDR, codecs, height, bitrate) are looked at again by the next scan.
- Finalize says what it really frees: the size of the kept originals, plus the saving against before re-encoding. "Finalize all" asks once and does the whole series in one call.
- Faster page: the web server runs at normal priority and only ffmpeg runs at nice 19; every ffmpeg gets a thread cap (`RX_FFMPEG_THREADS`, default 2) instead of one thread per host CPU, which kept the 4-CPU container throttled and ran it out of memory; the scheduler sleeps until a job ends instead of asking the database 20 times a second; the series summary is computed once per change instead of three times per poll.
- HDR: "Skip HDR videos" is on the Settings screen. Turned off, HDR10 and HLG files are re-encoded and stay HDR (colour tags kept, a copy that lost them is never kept), Dolby Vision drops to its HDR10 picture, profile 5 is always skipped, and the Compare frames of HDR files are tone-mapped instead of washed out.
- The Rescan button on a single series works again (it called a method that did not exist).
- No more crash in the progress line when ffmpeg reports N/A at the start of an encode (46 tracebacks a day in the log).

- Queue panel: see which series are encoded or measured next, drag to reorder (mouse or finger), ↑ to send one to the front, ✕ to take it out, and put it back from "Taken out". Every panel on both pages folds from its heading and then shows its numbers in one line; remembered per browser.
- Queue rows in the style of the CI queue page (circled number, library and action chips, accent edge on the next one); every library card and list row has a coloured edge for its state (done, ready to confirm, partly done, queued, running, skipped). Reordering only changes the group you moved in. "Encoding next" and "Measuring next" fold on their own too.
- "Up next" on a series card now says what it did: a note ("… is now first to encode" or "to measure" for a series not ticked for encoding) with a link to the queue, and the row flashes where it landed. The card itself keeps its place, since cards sort by name. Hovering the button no longer turns its text plum on dark.
- The series window starts fresh every time: no comparison or file list left over from the series opened before. Done files keep a Compare button (their frames are kept).

## 0.1.0 (2026-10-19)

First release: VMAF-searched AV1 re-encoding (QSV with a full-GPU path, VAAPI, NVENC, SVT-AV1), report / manual / stage
modes, staged copies with compare, confirm, undo and finalize, encoding_report.json per series, library and overview
pages that work on phones, every setting in docker-compose.example.yml.
