# Classic Release Manager V1 — frontend pilot

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

## Scope and rollout

This is a pilot for reviewed isolated frontend publication only. Existing direct
entry points remain available; the queue is not yet an enforcement boundary.
No automatic merging, automatic main rebasing, API/Runner/CUT queue integration,
background scheduler, or dashboard is enabled. Those require independent tests
and explicit rollout. Git synchronization is not production activation.
