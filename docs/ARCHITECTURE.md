# Package boundaries

This repository contains a local-first dataset editor with optional explicit Hub publication. It does not contain or
start the robot's VR collection, ROS controllers, Isaac Sim, or training jobs.

```text
ROS1 bag ── import/read-only ──┐
                              ├─ source/cache ─ project clips/tasks ─ export plan
existing LeRobot dataset ─────┘                                  │
                                                               ▼
                                                   new LeRobot v3 dataset
```

`desktop.py` hosts the same static UI as `app.py` in Qt/WebEngine, starts a private
loopback backend on an OS-assigned port, and provides native path/download dialogs.
The browser/server mode is still supported. Imported recordings remain read-only.
Explicit local append/replace publishes with a recoverable backup of the old target.

| Module | Responsibility |
|---|---|
| `core.py` | Frame intervals, labels, trim/split, gaps, episode/export plan |
| `checkpoints.py` | Per-episode metadata journal, immutable media inventory, interrupted-file quarantine |
| `dataset_lock.py` | Shared export/Hub writer lock, advisory exclusion and conservative dead-owner recovery |
| `store.py` | SQLite project revisions |
| `sources.py` | Read-only catalog and LeRobot adapters |
| `file_browser.py`, `static/file-picker.mjs` | Allowed local folders / shared multi-file selection dialog |
| `ssh_sources.py` | Existing OpenSSH aliases, read-only remote helper, resumable verified local downloads |
| `rsync_transfer.py` | Optional protected-argument rsync-over-SSH transport with SHA-256 verification; no remote mutation |
| `bag.py`, `ros1_reader.py` | ROS1 import and manufacturer message compatibility |
| `profiles/` | Robot-specific topic, units, sampling, feature mappings |
| `exporter.py` | Official LeRobot writer, new output, provenance, small reload |
| `export_runtime.py` | CPU-aware bounded ordered decode prefetch and encoder queue/thread settings |
| `inspection_cache.py` | Bounded process-local gap report cache with current file stamp invalidation; never caches consent |
| `repositories.py` | Named local datasets, append/replace preflight and backup publication |
| `hub.py` | Explicit Hub preview/publication, canonical file allowlist, remote HEAD guard |
| `integrations/lerobot.py` | Upstream writer/loader capability adapter and version provenance |
| `integrations/streaming.py` | Instance-local blocking queues and input/encoded video frame-count equality |
| `doctor.py` | Installed dependency checks and optional disposable export smoke |
| `jobs.py` | Isolated import/export/Hub workers and durable logs/status |
| `app.py`, `static/` | Shared editing UI and local API |
| `desktop.py` | Native window and dialogs, not a second editing engine |
| `tools/build_docs.py`, `docs/site/` | Generate GitHub Pages from the same Markdown guides |

## Data ownership

The code repository is independent of the runtime workspace and original data.
Use `--workspace` to open an existing workspace without moving it. Use
`--allow-root` to grant external data directories explicitly. Desktop file-dialog
selections can also grant access, remembered in `desktop-roots.json` in the workspace.

Do not put bags, videos, caches, model weights, project databases, datasets, exports,
credentials, or a virtual environment in Git. A source-only bundle is not a data
backup. The current project format contains absolute source paths: when moving
projects between PCs, preserve those paths or re-register the sources.

## Semantics

- Intervals are half-open frame indices `[start, end)`.
- Unlabeled frames are excluded; a task is not implicitly propagated into gaps.
- Adjacent labeled clips can share an episode, with task changes per frame.
- A temporal gap or an original episode boundary always starts a new episode.
- Optional feature gaps use validity masks, not fabricated measurements.
- Required numeric gaps must be excluded or resolved through the input profile.
- Tag/TF/session events remain sparse provenance annotations.
- Train/validation/test assignment is by original recording, not arbitrary clips.
- A policy-specific combined arm/gripper/base vector is a separate training adapter.
- Hub publication syncs a complete local snapshot, not a remote episode merge; local editor sidecars stay private.
- Local editing/import/export defaults offline. Explicit SSH browse/download uses the system SSH client;
  only dedicated Hub workers enable HF network access before importing that SDK.
- Depth editing, mixed-FPS auto-resampling, and training are not included.
