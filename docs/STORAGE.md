# Storage and lossless compaction

Preview images deliberately remain available after Export: open projects still
depend on them. Never delete the whole `jobs/` directory: imported catalogs and
their images can live there, alongside disposable export intermediates.

**프로젝트에서 원본 제거는 용량 정리가 아니다.** 복원 가능한 보관이므로 bag·미리보기 캐시·
annotation·편집 이력은 그대로 남는다. 같은 캐시를 쓰는 다른 프로젝트나 실행 중인 작업도 유지한다.
제거·복원 방법은 [원본별 상태 안내](SOURCE_STATUS.md#원본을-프로젝트에서-제거복원하기)를 참고한다.

## New writes

- Import preserves every configured event message in `events.jsonl.gz` (gzip
  level 1). No TF downsampling, numeric rounding, or frame removal is involved.
- Export stores immutable compressed event chunks plus `editor/events.index.json`.
  Append adds one new chunk rather than copying the complete cumulative event log.
  History/checkpoints share immutable chunks with hardlinks when supported; other
  filesystems fall back to copying. Old plain JSONL datasets remain readable.
- SQLite revision records reference content-addressed, compressed source manifests.
  Source descriptors are stored once per distinct value, rather than repeating
  every image path at each annotation edit. All Undo/Redo revisions remain intact.
  The current project JSON and public API remain unchanged.
- Only the latest two completed episode checkpoints of the same job/identity are
  retained, after atomically publishing the new checkpoint pointer. Failed saves
  do not prune the previous pointer. This is not video deletion.
- After the official writer closes, equal existing video/data files can share
  an inode with the recovery backup. Writable staging files are independent while
  the writer is active. Full bytes and fingerprint timestamps must match first.

The LeRobot `data/`, features, task indices, state/action values, masks and video
contents do not change. The event index is editor provenance, not a new LeRobot
feature. Hub publishing continues to exclude `editor/`.

## Existing workspaces: explicitly offline maintenance

First save pending browser edits. Stop every editor server/worker on that host;
the command refuses to apply while those processes are present. Do not start a
second server while maintenance runs. Use the updated code for every installation
sharing the affected caches. Do not use this as a general-purpose disk cleaner.

Plan only (does not change files):

```bash
bash run.sh storage-compact --root /absolute/editor-workspace \
  --root /absolute/shared-editor-cache
```

Apply, only after agreeing to the changes and downtime:

```bash
bash run.sh storage-compact --root /absolute/editor-workspace \
  --root /absolute/shared-editor-cache --apply
```

The same command is available as
`python -m kuavo_dataset_editor.storage_maintenance`. Include both editor
workspaces and the shared cache in a single run so hardlink aliases are handled
together. Original rosbag roots must not be included.

Maintenance:

1. Saves a SHA256-verified compressed SQLite rollback copy named
   `editor.sqlite.pre-storage-v1.gz` before the first migration.
2. Rewrites each legacy revision transactionally, verifies exact reconstructed
   project equality, keeps every revision, checks SQLite integrity, then VACUUMs.
3. Removes recovery checkpoints only for jobs with a published, complete output.
   Failed/unpublished job checkpoints and import images are retained.
4. Compresses event logs without dropping messages and verifies the decompressed
   SHA256 before removing plain files. Known hardlink aliases remain shared.
5. Shares identical `.mp4` files only when content, size, mtime, device and
   permissions match. This retains dataset fingerprint identity.

Imported LeRobot directory sources referenced by project history or saved job
requests are excluded from event-log rewriting, because changing provenance paths
would change their directory fingerprint. Catalog manifests that still refer to
`events.jsonl` are supported through a `.gz` fallback; no image relinking is needed.

The command logs JSON progress. It is intentionally not an automatic post-Export
cache wipe. Successful published outputs, images, annotations, all revision
history, and original bags are kept. Space needed temporarily includes a compressed
DB backup and one compressed event file at a time. Backup files remain available.

## Compatibility and recovery

Older editor code cannot read compacted SQLite revisions or event chunk indexes.
Keep the updated code installed; do not roll back just the Git branch. Before any
full rollback, stop the servers and retain the compacted workspace. The compressed
DB rollback copy restores the pre-migration database, but does not undo event-file
compression; those logs must also be decompressed for legacy code. No automatic
rollback or arbitrary cache garbage collector is provided.

On interruption, committed DB revisions remain readable with the updated code.
Compression never removes a plain event file before its verified compressed copy
exists. A crash between publishing some compressed aliases and unlinking plain
aliases may leave both formats; reruns conservatively report and skip those files
instead of guessing which copy to delete. Check the report before manual cleanup.

Measure actual allocation, not apparent pathname size (hardlinks count once):

```bash
du -sx -B1 /absolute/shared-editor-cache /absolute/editor-workspace
df -B1 /absolute/editor-workspace
```

Do not add nested `du` totals or independently scanned hardlink aliases together.

## Repair moved original bag paths

When a workspace/cache was copied from another PC, cached images may still be
viewable even though `original_path` points to the old workstation. Wholebody
Export needs the original bag to reconstruct torso/base values.

With servers stopped, preview and then explicitly apply a source-path repair:

```bash
bash run.sh relink-bags --workspace /absolute/editor-workspace \
  --bag-root /absolute/rosbags
# Add --apply after reviewing the plan. Repeat --bag-root for another original root.
```

Only missing original `.bag` paths of catalog sources are eligible. The extraction
cache must still exist. A unique candidate must match the saved fingerprint:
filename, size and nanosecond modification time. This is the existing metadata
fingerprint contract, **not** a full bag-content checksum. Missing, different or
ambiguous candidates are reported and left unchanged; no bag is copied or edited.

Only `original_path` changes. The complete project is verified to be identical
apart from approved path changes before adding one relocation revision per changed
project. Prior revisions remain intact; a `source_relocations` journal records old
and new paths. This does not relocate missing image caches or rewrite old job
requests. After a code/storage migration, start a new Export from saved annotations
instead of assuming a pre-migration interrupted job will resume across versions.

## Real-bag smoke validation (2026-10-26)

The updated integrated code was checked against the newest available primary
recordings of each type on the validation host:

- VR: `vr_trial_182_20261023_195130_457053.bag`.
- RWH: `rwh_auto_20261026_191654.bag`.

Each contributed a 60-frame interval (not a full long-bag conversion), producing
120 frames in two episodes with state 24 / action 23 and per-frame task changes.
The first/last frames of all three cameras decoded successfully. The compressed
event chunk contained 3,349 readable events. Missing rack-pose values retained
the existing validity-mask behavior; no interpolation was approved or applied.
Source bag size/mtime fingerprints were unchanged.

The VR extraction cache was reused after checking its bag fingerprint. That
cache still recorded an older workstation's original-bag mount path; only the
disposable test descriptor was pointed to the actual bag selected for this test.
Production project descriptors and annotations were not relinked by validation.
Generated test conversions and newly extracted test images were removed, keeping
the small validation report. Full Python regression: 163 passed, 9 skipped; docs
generation/link checks passed separately. Small official writer new/append/replace
and episode recovery tests also passed.

## Measured existing-workspace reduction (2026-10-26)

Actual allocated bytes were measured with `du -sx -B1` over the same three roots,
in the same order, so hardlink aliases count once. Original bags are excluded;
the retained SQLite rollback archives are included in the final footprint.

| Scope | Before (GiB) | After (GiB) | Freed (GiB) |
| --- | ---: | ---: | ---: |
| Shared import cache | 166.31 | 110.73 | 55.58 |
| RWH workspace | 199.27 | 39.66 | 159.61 |
| VR workspace | 97.46 | 17.82 | 79.65 |
| Total | **463.05** | **168.21** | **294.84** |

The total reduction is **63.7%**. Key contributors (not additional totals to sum):

- 207 unique event-file groups: 271.99 GiB raw to 33.98 GiB compressed, verified
  by decompressed SHA256 equality before removing the uncompressed aliases.
- Identical backup videos: 40.12 GiB freed through content-checked sharing.
- The two annotation databases: 11.97 GiB to 159.12 MiB, with every historical
  revision reconstructed and checked during migration.
- Completed-export recovery checkpoints removed only where a complete published
  output remained; failed/unpublished job artifacts were conservatively retained.

All 11 saved current-project JSON payload hashes are identical before/after; all
969 historical revisions remain. Preview images were not deleted. The workspaces
were not combined, annotations were not transferred between them, and the original
bag files were not edited. Per the user's later instruction, the two old editor
servers were not restarted.

An additional relocation plan found 59 missing original bag filenames in the
copied VR workspace and no matching originals in the checked primary, auxiliary,
or discarded bag folders. Therefore **zero paths were rewritten**. Their images
and annotations remain, but wholebody re-derivation requires restoring those
original bags. Their absence was a pre-existing condition, not a result of this
compaction; preserving preview caches is especially important for these sources.
