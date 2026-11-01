# Security policy

Lyrveil is a local, single-user application. It keeps private conversations and
images on the owner's machine, so the project treats data exposure and container
escape as serious defects even though no public service exists.

The threat model and the controls are in [docs/security.md](docs/security.md).

## Supported versions

| Version | Supported |
| --- | --- |
| No release yet | Design documentation only; no code exists |
| Latest release, once published | Yes |

## Reporting a vulnerability

Use GitHub private vulnerability reporting on this repository: open the
**Security** tab and choose **Report a vulnerability**. Do not open a public issue.

Include the affected version, the steps to reproduce, and the impact. Never attach
real conversations, memories, generated images or credentials. Reproduce with
synthetic data.

The maintainer acknowledges a report within 7 days and publishes the fix with a
changelog entry. The report stays private until a fix is available.

## In scope

- Access to the application from another origin or from the network.
- Paths that give a browser or a model output control over Docker, the host
  filesystem or another runtime.
- Leaks of conversation content into logs, images, backups or commits.
- Upload handling, path traversal and unsafe file writes.

## Out of scope

- A person with administrator access to the same Windows or WSL2 account.
- Text or images the local models produce when they respect the documented
  safeguards. Report a safeguard bypass as a normal safety issue.
- Vulnerabilities in KoboldCpp, ComfyUI, Docker or the NVIDIA stack. Report these
  upstream. Report here when Lyrveil's configuration makes them reachable.
