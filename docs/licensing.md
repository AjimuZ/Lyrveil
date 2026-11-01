# Licensing

Status: verified from upstream sources on 2026-11-01. This page is engineering documentation, not legal advice.

## Lyrveil

Original code and documentation are licensed under the [Apache License 2.0](../LICENSE). See [ADR 0002](decisions/0002-license-apache-2.md). The repository contains no third-party code, runtime or weights.

## AI runtimes

| Runtime | License | How Lyrveil uses it |
| --- | --- | --- |
| KoboldCpp `v1.124.1` | GNU AGPL v3.0, unless a file states otherwise. Bundled GGML, llama.cpp and stable-diffusion.cpp are MIT, and audio.cpp is Apache 2.0 | Separate process, HTTP only, unmodified |
| ComfyUI `v0.43.0` | GNU GPL v3.0 | Separate process, HTTP only, unmodified |

Consequences:

- The Lyrveil repository holds Dockerfiles that download these programs on the user's machine at build time. It does not distribute them.
- Lyrveil's code talks to them over HTTP and links none of their code, so it stays under Apache 2.0.
- **Anyone who publishes prebuilt `lyrveil/llm` or `lyrveil/image` images distributes AGPL and GPL software.** They must meet those licenses, including offering the corresponding source. The project publishes no such images in 1.0.
- A modified KoboldCpp offered over a network would fall under AGPL section 13. Lyrveil does not modify it.

PyTorch and the NVIDIA CUDA libraries installed in those images keep their own licenses. Lyrveil does not redistribute them.

## Models

Weights are never in the repository or in an image. `setup.sh` shows the following notice and asks for acceptance before downloading from the upstream repositories.

### Rocinante 12B v1.1

| Item | Value |
| --- | --- |
| Author | TheDrummer, with BeaverAI |
| GGUF quantization | bartowski |
| License field | `other` on both the original and the GGUF card. **Neither card gives license terms.** |
| Base model | Mistral Nemo Base 2407, Apache 2.0 |

The terms of this finetune are not stated. Therefore:

- Lyrveil never redistributes the file. Users download it from its public upstream repository.
- The user is responsible for deciding whether their use is acceptable.
- Any commercial use or redistribution needs clarification from the author first.

### Illustrious XL v1.0

| Item | Value |
| --- | --- |
| Author | OnomaAI Research |
| License field | `other`, named `sdxl-license`, linking to the SDXL Base 1.0 license |
| License | CreativeML Open RAIL++-M, dated July 26, 2023 |

Summary in our words. Read the license itself.

- **Use restrictions apply to every user.** The model and derivatives must not be used to break the law, exploit or harm minors, spread false information intended to harm others, spread harmful personal information, defame or harass, make fully automated decisions that
  adversely affect legal rights, discriminate, exploit vulnerabilities of groups, give medical advice or interpret medical results, or produce information for justice, law-enforcement, immigration or asylum processes.
- **Output.** The licensor claims no rights in generated images. Users are responsible for their output and its use.
- **Distribution.** Distributing the model or derivatives, including hosting for third parties, requires passing on the restrictions, a copy of the license and modification notices. Lyrveil does not distribute it. LAN or hosted use is unsupported.

Lyrveil's safeguards support the restriction on minors ([ADR 0018](decisions/0018-adult-only-characters-and-content-safeguards.md)). They do not replace the user's obligations.

## Application dependencies

Versions and individual licenses are in [Technology choices](technology.md). All runtime dependencies use permissive licenses.

| License | Packages include |
| --- | --- |
| MIT | FastAPI, Pydantic, SQLAlchemy, Alembic, Angular, Pillow (MIT-CMU), structlog (dual) |
| BSD-3-Clause | Uvicorn, HTTPX, websockets, Jinja2, snowballstemmer, nvidia-ml-py |
| Apache-2.0 | RxJS, TypeScript, python-multipart, tzdata |
| PSF-2.0 | Python |
| MPL-2.0 | Hypothesis and `@axe-core/playwright`, development only |
| GPL-3.0 | shellcheck, development only, not distributed |

### Policy

P09 generates `THIRD_PARTY_NOTICES.md` from `pip-licenses` and `license-checker-rseidelsohn`.

| Scope | Allowed | Otherwise |
| --- | --- | --- |
| Production Python and npm packages | MIT, BSD, ISC, Apache-2.0, PSF, Zlib, Unlicense, OFL for fonts | Refused until reviewed in an ADR |
| Development only | Also MPL-2.0 and GPL tools that are not distributed | Listed |

`ng build` writes `3rdpartylicenses.txt` to the frontend bundle, and the frontend image serves it.

## Fonts

All are bundled from `@fontsource` packages, unmodified.

| Font | License | Use |
| --- | --- | --- |
| Inter | SIL OFL 1.1 | Interface and messages |
| Pixelify Sans | SIL OFL 1.1 | Brand and short titles |
| JetBrains Mono | SIL OFL 1.1 | Identifiers and diagnostics |

The OFL license text ships with the fonts in the image. P08 records package versions, file hashes and license texts in the notices.

## Graphic assets

Icons and the logo are original, drawn for Lyrveil and licensed under Apache 2.0. No third-party icon set is used. README screenshots use only the original demo characters and synthetic data. Generated demo images follow the Illustrious license above, and P08 records their prompts.

## Docker and NVIDIA

Docker Desktop and the NVIDIA driver are prerequisites that the user installs. They have their own terms. Docker Desktop is free for personal use, and larger organizations should check its subscription terms. Lyrveil distributes neither.

## Names

KoboldCpp, ComfyUI, Rocinante, Illustrious, Mistral, NVIDIA, CUDA, Docker and Angular are names of their owners, used only to describe compatibility. Lyrveil is not affiliated with them.

## Attribution

The README acknowledges these projects: KoboldCpp and KoboldAI Lite, llama.cpp and GGML, ComfyUI, Rocinante by TheDrummer, the GGUF by bartowski, Mistral Nemo by Mistral AI, Illustrious XL by OnomaAI Research, Stable Diffusion XL by Stability AI, Inter, Pixelify Sans and JetBrains Mono.
