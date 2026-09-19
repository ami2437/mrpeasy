# Label Tools Rollback and Data Protection Plan

Created: 2026-09-10

## Protected baseline

The pre-implementation snapshot is stored at:

`C:\mrpeasy\rollback_backups\pre-label-tools-20260910-155850`

It contains:

- `mrpeasy.db`: consistent SQLite backup made with `sqlite3.Connection.backup`
- `source/`: byte-for-byte copies of every existing source file expected to be touched
- `pre-existing-working-tree.patch`: the complete pre-existing Git diff
- `baseline-git.txt`: branch, HEAD commit, and working-tree state
- `verification-report.json`: backup verification details

Git baseline:

- Branch: `main`
- Commit: `0f42ea55285679c5b3f12a4fb5814b368861aa1b`
- Pre-existing modified files include `backend-fastapi/app/routes/labels.py`, `backend-fastapi/app/services/mrpeasy_client.py`, and `frontend/public/labels-batch.html`.

Do not use `git checkout`, `git reset`, or restore from HEAD to roll back this feature. Those operations would discard changes that existed before implementation.

## Saved-data baseline

Both the live database and backup passed `PRAGMA integrity_check` with `ok` and had no WAL or SHM sidecars.

| Table | Rows | Logical SHA-256 |
| --- | ---: | --- |
| `shipment_boxes` | 1003 | `2d9d4babe8bb8f3901bbdaceb33b0bd5f7c7de0aeda0a64abf83545cd7f19172` |
| `pack_sizes` | 26 | `1446f187885045b7bc582a1d4adc52d7a3ac94b32885927c83ff5b0438a53e4c` |
| `labels` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

Packing-list data is derived from `shipment_boxes`, so the 1,003 finalized box records are the protected packing-list source data.

Other table counts at baseline: `customer_orders=100`, `inventory=0`, `manufacturing_orders=0`, `roles=0`, `stock_items=0`, `sync_logs=1`, `users=1`, and `vendors=0`.

## Implementation safeguards

1. Do not change the database schema or models for this feature.
2. The finalized-label API must be `GET` and read-only.
3. On-demand edits must stay in browser memory and must not call finalize, sync, update, or delete endpoints.
4. Item-level and cross-shipment printing may call only the existing read endpoints and `/api/labels/generate/{shipment_code}`, which does not persist labels.
5. Backend tests must use a temporary database/session and must never point at `mrpeasy.db`.
6. Run database integrity, counts, and logical fingerprint checks after backend work and again before completion.
7. If any protected fingerprint changes unexpectedly, stop implementation immediately and investigate before doing further work.
