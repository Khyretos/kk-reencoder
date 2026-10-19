# Changelog

## Unreleased

- Queue panel: see which series are encoded or measured next, drag to reorder (mouse or finger), ↑ to send one to the front, ✕ to take it out, and put it back from "Taken out". Every panel on both pages folds from its heading and then shows its numbers in one line; remembered per browser.
- Queue rows in the style of the CI queue page (circled number, library and action chips, accent edge on the next one); every library card and list row has a coloured edge for its state (done, ready to confirm, partly done, queued, running, skipped). Reordering only changes the group you moved in.

## 0.1.0 (2026-10-19)

First release: VMAF-searched AV1 re-encoding (QSV with a full-GPU path, VAAPI, NVENC, SVT-AV1), report / manual / stage
modes, staged copies with compare, confirm, undo and finalize, encoding_report.json per series, library and overview
pages that work on phones, every setting in docker-compose.example.yml.
