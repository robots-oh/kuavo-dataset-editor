# Storage and lossless compaction

Preview images deliberately remain available after Export: open projects still
depend on them. Never delete the whole `jobs/` directory: imported catalogs and
their images can live there, alongside disposable export intermediates.

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
