# CUT usage lifecycle — isolated repair (2026-11-23)

## Problem and authoritative evidence

The 5176 Classic database contained 499 completed CUT tasks whose USD reservation remained `RESERVED`, plus 65 locally failed CUT tasks in `RESERVED`. The existing CUT worker updates the edit task but never updates its `AccountUsage` record.

Two previously recovered one-clip tasks both have unique CUT IDs, `completed` NAS status with revision 3, matching manifest digests and verified output records. Their original two-clip reservations had already been `RELEASED`; the new one-clip cost for each was USD 0.15.

`AccountUsage` is conservative **budget evidence**, not the external CUT provider invoice.

## New transitions

- `QUEUED`, `READY`, `ACCEPTED`, `EDITING`, network timeouts or unknown status: keep `RESERVED`.
- CUT result verified and output imported, with local task status `PENDING_REVIEW`: change existing reservation to `ESTIMATED`. This confirms local estimated consumption, **not** externally invoiced settlement.
- CUT task with terminal `FAILED`: change `RESERVED` to `UNCERTAIN`. **Do not automatically refund or release** without verified no-charge evidence.
- `RELEASED`: only after confirmed non-charge/explicit release path; no generic failure auto-release.
- All transitions update the **existing single** `AccountUsage` record using its unique CUT operation ID, under the same DB transaction and the existing task lease fence. No additional reservation is created.

## Historical repair policy

`backend/scripts/reconcile_classic_cutflow_usage.py` is dry-run by default. `--apply` requires a distinct SQLite online backup file.

The tool only promotes completed records to `ESTIMATED` if each has:
1. Imported output metadata attached to the same CUT task.
2. An immutable inbox manifest whose file hash matches the CUT `completed` authority.
3. Matching task identity, policy receipt, revision and stable batch identity.

Historical `FAILED` records become `UNCERTAIN` (not `RELEASED`), preserving their amount and flagging the need for CUT-side billing evidence. All other tasks remain untouched. Updates are idempotent and compare previous status and CUT task state; remote NAS reads occur outside SQLite write locks.

## Release constraints

- Work on the isolated branch first. Do not modify active CUT work or force a full 5176 restart.
- Back up the live database using SQLite's online backup API; verify quick_check before modification.
- Run full dry-run, test backfill on a copy, repeat to verify zero second-pass changes and unchanged `budgetUsedUsd`.
- For worker deployment, reload **only** the 5176 CUT worker at a verified idle window and preserve the 5176 frontend and generation Runner.
- Verify account counts, existing source idempotency, API/worker health and front-end usage after release.
- The 65 failed tasks require manual financial reconciliation; the script does not assert whether CUT charged them.
