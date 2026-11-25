# Image profiles on the GPU: T-GPU-06 and the measurement of T-IMG-14, 2026-11-25

GPU.5. `tests/gpu/ImageProfiles.Tests.ps1` started ComfyUI on the reference machine once for each catalog image
profile and rendered three **Rapida** and three **Alta** images with each. It measured the checkpoint load, the render
time and the peak VRAM of each preset. The preset rule of [ADR 0026](../decisions/0026-two-pass-image-rendering.md),
computed by `backend/tests/gpu_probe/image_presets.py`, keeps **Alta** as the default quality preset. Every check
passed, and the level removed its project and data volume.

## Setup

- Machine: NVIDIA GeForce RTX 4070, 12282 MiB, with 856 MiB in use by the desktop before the run (NVML). Windows,
  Docker Desktop.
- Images: `lyrveil/image:gpu-test` (ComfyUI 0.43.0 at the pinned commit, every layer from the build cache), and the
  backend's test stage as `lyrveil/backend-test:gpu-test`.
- Checkpoints: the three catalog files from the installation's `lyrveil-models` volume, mounted read-only. The test
  data volume was `lyrveil-gpu-test-data`, in the project `lyrveil-gpu-test`. No installation runtime was running, and
  neither `lyrveil-data` nor the backups were touched.
- Graphs: `sdxl_base.v2.json` (**Rapida**) and `sdxl_hires.v1.json` (**Alta**), built by `build_workflow` with
  `ImageSettings.for_profile`, as the `render` stage builds them. The settings were the documented defaults: 28 steps,
  CFG 5.5, `euler_ancestral` with `normal`, CLIP skip 2, and for **Alta** 14 refine steps at `denoise` 0.45. Each
  profile used its prompt preset's tags and the safety negative, with one fixed scene of an adult character. The aspect
  was square, the largest final size of each preset. **Rapida** used seeds 101, 102 and 103; **Alta** used 201, 202 and
  203.
- Order, for each profile: wait for the VRAM to fall back to the baseline, start `lyrveil-image` alone, render the
  three **Rapida** images, then the three **Alta** images, and remove the runtime. A background job sampled NVML
  every 250 ms, separately for each preset.

## Commands and results

```text
scripts\lyrveil.cmd test gpu ImageProfiles illustrious-xl-v2.0   # first run, one profile: 8 passed, 118.44 s
scripts\lyrveil.cmd test gpu ImageProfiles                       # all three profiles: 16 passed, 338.01 s
```

Both commands ended with `no container or volume of the test project is left` and `test gpu: passed`. The figures
below come from the second run's results file, `test-results/gpu/image-profiles-20261125-224856.json`. Git ignores
that file, so it is not in the repository.

## T-GPU-06: ComfyUI start, real generation, checkpoint load

| Profile | Ready | First Rapida render (loads the checkpoint) | Later Rapida renders | Checkpoint load |
| --- | --- | --- | --- | --- |
| `illustrious-xl-v2.0` | 5.1 s | 16.05 s | 9.03, 9.06 s | 7.01 s |
| `illustrious-xl-v1.0` | 7.0 s | 18.70 s | 9.03, 9.07 s | 9.65 s |
| `noobai-xl-1.1` | 7.5 s | 19.70 s | 9.03, 9.05 s | 10.66 s |

- **Ready** is the time `up --wait` took until the health check passed. `/system_stats` reported a `cuda` device, the
  RTX 4070 with `cudaMallocAsync`, and ComfyUI 0.43.0.
- **Checkpoint load** is the first render's time minus the median of the later renders of the same graph. ComfyUI's
  own execution timestamps give 6.80, 9.65 and 10.62 s.
- In the first run, `illustrious-xl-v2.0` loaded in 9.6 s. The second run probably found the file in the host's file
  cache, but that cause was inferred, not measured. The cache state was not controlled in either run, so a load from a
  cold disk can take longer.
- All 18 renders ended with ComfyUI status `success` and saved one PNG at the preset's final size: 1024 x 1024 for
  **Rapida** and 1536 x 1536 for **Alta**. The size was read from the PNG header. ComfyUI ran with `cap_drop: ALL`
  and a read-only root, as in `compose.yaml`, and needed no writable path beyond its tmpfs (A-04).

## T-IMG-14: render time and peak VRAM per preset

| Profile | Rapida render | Rapida peak | Alta render | Alta peak | Alta margin |
| --- | --- | --- | --- | --- | --- |
| `illustrious-xl-v2.0` | 9.03 to 9.06 s | 10479 MiB | 21.06 s (3 times) | 11631 MiB | 651 MiB |
| `illustrious-xl-v1.0` | 9.03 to 9.07 s | 10511 MiB | 21.06 s (3 times) | 11631 MiB | 651 MiB |
| `noobai-xl-1.1` | 9.03 to 9.05 s | 10575 MiB | 21.05 to 21.06 s | 11631 MiB | 651 MiB |

- A render time is the client's time from submitting the graph to seeing it complete. The client polls every 0.5 s.
  ComfyUI's own timestamps give 8.88 to 8.93 s for **Rapida** and 20.63 to 20.72 s for **Alta**.
- A peak is NVML's highest used memory on the whole card while that preset rendered, so it includes the desktop's
  856 MiB. The margin is 12282 MiB minus the peak. The **Rapida** peak includes the checkpoint load.
- The **Alta** peak was 11631 MiB for every profile. That suggests ComfyUI's memory management sets the peak, not
  each checkpoint's own need. This was not investigated. No render ran out of memory, so the tiled workflows were not
  needed on this machine, and A-05 holds at square, the largest size.

## The preset rule

`rule_document` of `backend/tests/gpu_probe/image_presets.py` took the measured times of each profile.
`backend/tests/unit/test_gpu_image_probe.py` tests it: 11 tests, which passed in the `unit` level with
`scripts\lyrveil.cmd test unit tests/unit/test_gpu_image_probe.py`. The rule takes the nearest-rank 95th percentile
of the **Alta** times. **Alta** stays the default when that percentile is at most 45 s. It leaves out the first
render after a start, which also loads the checkpoint.

| Profile | Alta p95 (samples) | Rapida p95 (samples) | Verdict |
| --- | --- | --- | --- |
| `illustrious-xl-v2.0` (target default) | 21.06 s (3) | 9.06 s (2) | `alta` |
| `illustrious-xl-v1.0` | 21.06 s (3) | 9.07 s (2) | `alta` |
| `noobai-xl-1.1` | 21.06 s (3) | 9.05 s (2) | `alta` |

The default quality preset stays **Alta** (`default_preset: alta` for `illustrious-xl-v2.0`, the default profile of
the target tier). A-13 holds on this measurement.

## Limits

- Three **Alta** renders per profile are few. With fewer than 20 samples, the nearest-rank 95th percentile is the
  largest value. The times varied by less than 0.1 s, so more renders would probably not change the verdict, but that
  was not measured.
- Only the square aspect ran. Portrait, landscape, tall and wide are smaller, so they were not timed.
- The render time excludes the checkpoint load. An **Alta** image that is the first one after a mode switch would
  take about 21 s plus 7 to 11 s, still under 45 s. That is derived from the two measurements: no **Alta** render ran
  first after a start.
- The test is a ComfyUI client of its own. The backend's `ImageGenerationService` (progress over the WebSocket, the
  fetch into staging, persistence) did not run, nor did a mode switch (T-GPU-07), the tiled workflows, the grid or
  the blind review (T-QLT-02).
- The images were not looked at. They stayed in the runtime's tmpfs and were removed with it.
