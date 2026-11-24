# Classic release: simple everyday entry

Run in clean Classic production main after isolated testing and diff review:

```text
python scripts/classic_release.py deploy
python scripts/classic_release.py deploy --apply --reviewed-api-compatible
```

Preview is read-only; apply is an explicit approval of the exact reviewed UI/API
combination. Optionally pin the reviewed main via `--expected-main-head` and
active API via `--expected-api-revision`. No manual receipt, queue or candidate
parameters are required. The command discovers the live baseline, skips an
already-current frontend, prepares a new isolated worktree, installs locked
npm dependencies, builds and uses existing atomic publication/rollback and
receipt verification. A frontend success is not full backend activation.

For an already-installed reviewed API-only standby, use:

```text
python scripts/classic_release.py deploy --api-only
python scripts/classic_release.py deploy --api-only --apply --reviewed-api-compatible --expected-api-revision <previewed-target-sha64>
```

API target must be explicitly reviewed and pinned; no automatic rollback to an
arbitrary standby. No new API process is started by this command. Existing
strict Worker/schema/prompt procedures remain. The durable queue is internal;
a pending or uncertain existing release must be inspected before new work, and
only the command's own intent can be claimed. No background service, no fixed
waiting period. If a command fails, inspect the reported blocker and live state;
never replay an uncertain activation. This tool does not bypass readiness checks.

Stop expanding the release platform. Keep the existing detailed commands below
for diagnostics and exceptional recovery, not everyday operation.

---

# Classic Release Manager — coordinated release pilot

Production entry remains 5176. This pilot does not change admission, paid-task
processing, API routing, worker code, prompts, or the production task database.

## Fast path

1. Develop/test in independent worktrees. Run the existing
   `python scripts/classic_release.py plan-live-frontend` in clean main.
2. Review the exact frontend/API contract; prepare the isolated candidate using
   the existing pinned prepare-isolated workflow. Install locked dependencies and
   test. Do not reuse a candidate directory for another change.
3. From production main, enqueue the exact reviewed candidate:
   ```text
   python scripts/classic_release_manager.py enqueue-frontend --candidate-root <path> --change-base <sha40> --approved-frontend-head <sha40> --expected-main-head <sha40> --expected-candidate-head <sha40> --expected-api-revision <sha64> --expected-old-frontend <sha64> --reviewed-api-compatible
   python scripts/classic_release_manager.py status
   python scripts/classic_release_manager.py run-next --apply
   ```
   There is no timed batching or daemon. One explicit invocation runs one job.
   Other sessions can develop, test and enqueue immediately. The queue is separate
   from the task database, using durable SQLite intents and a single OS lease.
   Identical intents are deduplicated.
4. Build the isolated candidate before acquiring the shared release/route locks;
   a per-candidate lock prevents competing builds in that checkout. Verify pinned
   staged bytes and all existing provenance/compatibility gates inside activation.
   Retain the previous index and immutable assets. Only a successful publisher
   result with a receipt is recorded as verified.

## Failures and reconciliation

Stale pins are blocked; replan and enqueue a new reviewed candidate. If a
transient blocked release is still valid, use
`retry-blocked <id> --apply` (all gates run again). Exceptions are conservative:
needs-review or an interrupted running entry stop further queue execution.
Use `reconcile <id> --apply` only when the coordinator is not running and
the live frontend receipt is independently verified. This records a verified,
blocked or reconciled outcome; it never writes the live frontend or replays a
release. If a legacy lock remains after a crash, inspect its owner and actual
production state before any manual cleanup; never auto-delete locks.

## Phase 2: integration, safe repinning and API-only queue

Use the guarded fast-forward integration entry point after tests in an isolated
worktree: `python scripts/classic_release_manager.py integrate --expected-main <sha40> --tested-candidate <sha40> --apply`.
A new main commit or dirty source blocks promotion. For divergent candidates,
compose and test a fresh isolated integration commit before promotion. It does
not automatically merge unreviewed changes, and does not activate production.

The frontend queue can advance its main pin before build and again before
activation only when the approved frontend tree is unchanged across an ancestral
main fast-forward. The original pin is recorded in the release receipt. It does
not modify the approved frontend, active API or expected live index. A change
while activation is in progress still fails closed. All standard Python frontend
activation paths now acquire the existing release and route operator locks in
that order, so they cannot overlap supported API/Worker route mutations.

Only for a reviewed, already installed online API-only pair, with the exact
frontend/target API compatibility reviewed, enqueue:

```text
python scripts/classic_release_manager.py enqueue-api --current 8002 --target 8003 --active <sha64> --target-revision <sha64> --frontend <sha64> --config <sha64> --schema <schema-id> --reviewed-api-compatible
python scripts/classic_release_manager.py run-next --apply
```

The ports above are illustrative: read actual live identities first. This invokes
existing `classic_api_hot_switch.mjs --online-api-only` dry-run and apply guards,
not a new implementation of routing. It verifies post-switch health, source,
config, schema, open admission and unchanged frontend index. An existing proven
frontend receipt may be carried forward to the exact reviewed API combination
as an explicitly labelled compatibility receipt (not a new frontend publication).
Without prior proof, no frontend receipt is invented; reconciliation is required.
The API queue does not install slots, change workers, migrate schema, pause intake,
or touch the task database. API uncertainty is never blindly replayed.

## Scope and rollout

The coordinator supports reviewed isolated frontend releases and reviewed
online API-only switches. No background scheduler is enabled. Direct low-level
entry points and OS access still exist, so this is a cooperative workflow, not
an unbypassable permission boundary. Existing strict/Worker and schema release
paths remain available with their existing gates; do not run them concurrently
with a coordinated release. Automatic combined changes, independent component
manifest, full Worker queue integration and live release-time measurements
remain separate work. Source synchronization is not production activation.
