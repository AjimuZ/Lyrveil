# Image grid and identity protocol, smoke run, 2026-11-26

Status: recorded 2026-11-26 for QLT.3, the first run of `tests/quality/ImageGrid.Tests.ps1`, which automates T-QLT-02
(the [image default grid](../image-generation.md#grid)) and T-QLT-07 (the
[image identity protocol](../image-generation.md#image-identity-protocol) with its
[measured levers](../image-generation.md#measured-levers)). It was a **smoke run** (`short`) on the default image
profile. It proves that the generation, the image export, the blind sheet and the import work against the real stack. It
is **no evidence for the catalog**: no full run has been made, no owner has reviewed a sheet, and so no T-QLT-02 or
T-QLT-07 verdict, default rule or lever decision exists.

## What ran

```text
scripts/lyrveil.cmd test quality ImageGrid short illustrious-xl-v2.0
scripts/lyrveil.cmd test quality-rating test-results/quality/image-short-20261126-215948/sheet.md
```

- Machine: NVIDIA GeForce RTX 4070, 12282 MiB as read by `nvidia-smi` (643 MiB in use at the start). Pester 5.7.1 under
  Windows PowerShell 5.1. Project `lyrveil-gpu-test`, data volume `lyrveil-gpu-test-data`, `lyrveil-models` read-only.
- `Pester 5.7.1: 5 passed, 0 failed, 0 skipped`, `test quality: passed`, 305.8 s. The run ended with `[ok  ] quality:
  no container or volume of the test project is left`.
- The short mode plans 13 renders on the default profile (`illustrious-xl-v2.0`): the grid's six settings and the
  landscape, tall and wide sizes of one character, one scene and one seed (9), the identity baseline and the weighted
  render (2), and PAG at scale 1.5 and 3 (2). All 13 rendered with ComfyUI status `success`, one image of the final
  **Alta** size each, and were copied out as JPEGs.

| Render | Seconds (client side) |
| --- | --- |
| default (first, loads the checkpoint) | 30.7 |
| CLIP skip 1, CFG 4.5, CFG 7, denoise 0.35, landscape, tall, wide, identity baseline, identity weight | 21.05 to 21.55 |
| denoise 0.55 | 11.5 (ComfyUI reused the first pass of the render before it, which differs only in the second pass's denoise) |
| PAG 1.5 and PAG 3 | 25.56 each |

One sample per PAG scale is no 95th percentile. The one PAG render is about 4.5 s slower than the baseline at the same
size, in line with the extra model evaluation per first-pass step.

## What it produced

`test-results/quality/image-short-20261126-215948/` (Git ignores it): `sheet.md` with 9 grid images, 2 identity images, 2
PAG pairs and 1 identity-weight pair, 17 images under opaque names, `key.json` and `renders.json`. The sheet names no
profile, setting or lever. One image was read back to check that the render is a plausible picture of the character
(red short hair, green eyes, a canvas-like jacket, a brass compass). Whether it passes the owner's attribute checks is
the owner's call.

`scripts/lyrveil.cmd test quality-rating` on the unfilled sheet read the key, reported every decision `incomplete`,
listed the 14 blank ids, wrote `verdict.json` and exited 1, as it must. No filled sheet was imported, and no answer was
invented to exercise the import on the real files.

## What has not run

- The full run: `scripts\lyrveil.cmd test quality ImageGrid`. Per profile the grid is 3 characters, 2 scenes and 3 seeds
  at 6 settings (108 renders); the default profile adds the 3 other sizes (54), the identity protocol with and without
  the weight (120) and PAG at two scales (40): 538 **Alta** renders in all, about 21 to 26 s each, so roughly three and
  a half hours of the GPU, plus the owner's review of 558 items (378 grid images, 120 identity images, 40 PAG pairs and
  20 weight pairs).
- The grid on `illustrious-xl-v1.0` and `noobai-xl-1.1`, so the default rule across profiles.
- The owner's review, and so every verdict: the default rule, the three parameter decisions, the sizes, the identity
  accuracy, the identity weight and the PAG scale.
- The gap between each text profile and the default (T-QLT-07's second target): the scenes are fixed, not written by
  the text profiles, so this part is recorded as `not_run`.
- The Pester unit tests `tests/scripts/ImageQuality.Tests.ps1`, the new cases in `tests/scripts/QualityLevel.Tests.ps1`
  and `tests/scripts/TestCommand.Tests.ps1`, and the Python tests `backend/tests/unit/test_gpu_image_quality_probe.py`:
  the `unit` level runs them all together and was left to the orchestrator, so those files are written but have never
  executed. `scripts/lyrveil.cmd test lint` passed (documentation checks, PSScriptAnalyzer on 54 files, ruff, ruff format
  and mypy on the backend, with 0 findings), after two PSScriptAnalyzer findings in the new Pester file were fixed.
