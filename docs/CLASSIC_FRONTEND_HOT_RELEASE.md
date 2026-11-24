# Classic 5176 frontend-only hot release

Use only for reviewed, API-compatible frontend changes. Freeze the candidate main
commit and the active API source fingerprint, review all unpublished frontend
changes, build and test, and pin the existing live index SHA256. No backend,
worker, routing, database, prompt or billing change is authorized by this mode.

```powershell
python scripts/classic_release.py plan --change-base <approved-prior-commit>
python scripts/classic_release.py publish --change-base <approved-prior-commit> --apply --expected-main-head <40hex> --expected-api-revision <64hex> --expected-old-frontend <64hex>
```

Task counts and task preflight warnings remain visible, but do not block this
specific mode. The strict default remains unchanged. The release lock, clean
main, active backend fingerprint, readiness, pinned live entry and unchanged
route/config/schema checks remain required. Build stages only; publish retains
previous hashed assets and backs up the previous index. If verification fails,
inspect the backup and roll back the index atomically without touching jobs or
the database. Refresh an existing tab to load the new version.


## Read-only live baseline discovery

```powershell
python scripts/classic_release.py plan-live-frontend
```

This command reads the current 5176 route, live API fingerprint, served index and
matching successful receipt, and finds the actual approved frontend ancestor.
If the frontend tree is already live it returns `frontend-already-current`,
even when unrelated backend code is pending. Otherwise it provides pinned
`prepareArgs` for the existing isolated candidate workflow after API compatibility
review. A changed or missing receipt, dirty main, unhealthy route, or non-UI
contract change blocks the plan. The command does not publish or approve a change.
Strict full-release verification remains mandatory for backend releases.

## Independent candidate when main contains pending API or Worker changes

The strict main publisher still requires full main/API consistency. Do not clear
that blocker or switch the API to make a UI release green. Use the independent
candidate gate instead. The current frontend must have a local successful
release receipt in release-history matching its served index and frontend tree.
If no receipt exists, recover the actual approved release provenance first;
do not fabricate one from main or from a guessed hash.

1. Read classic_active_release_status.py --json. Pin main HEAD, active API
   root/commit/fingerprint, and the live index hash. Identify the prior approved
   frontend commit from its verified release receipt.
2. Review the complete frontend diff from that approved base to the desired
   approved frontend commit (an ancestor of current main). Use a fresh isolated
   worktree based on the active API commit, and restore ONLY the reviewed
   frontend tree from the approved commit. Commit the candidate. Review its
   complete non-frontend diff (must be empty). Install locked frontend
   dependencies in that candidate and run relevant tests/lint.
   The unified entry can create this candidate automatically (the directory
   must not exist; this command does not publish):

   ```powershell
   python scripts/classic_release.py prepare-isolated --apply --candidate-root <new-worktree> --change-base <previous-approved-frontend> --approved-frontend-head <new-approved-frontend> --expected-main-head <main> --expected-api-revision <running-api> --expected-old-frontend <served-index>
   ```

3. Use the returned candidate commit and the same pinned arguments for plan and publish:

```powershell
$pin = @(
  '--candidate-root', '<isolated worktree>',
  '--expected-candidate-head', '<40-hex candidate>',
  '--change-base', '<40-hex previous approved frontend commit>',
  '--approved-frontend-head', '<40-hex new approved frontend commit>',
  '--expected-main-head', '<40-hex current main>',
  '--expected-api-revision', '<64-hex running API>',
  '--expected-old-frontend', '<64-hex served index>'
)
python scripts/classic_release.py plan-isolated @pin
python scripts/classic_release.py publish-isolated @pin --apply --reviewed-api-compatible
```

The isolated gate requires a clean, same-repository checkout; the exact approved
frontend tree; an API-identical non-frontend tree and runtime fingerprint;
a live matching receipt; an open intake gate; and unchanged main, API slot,
API root, configuration, schema and served entry before switch. Only the
frontend diff is classified for this mode; excluded main paths are reported,
not published. Frontend contract, prompt, dependency, schema or Worker changes
are not approved by a path-only UI classification. New immutable assets are
installed before the entry is atomically replaced; collisions and changed
existing public assets fail closed. Every published hashed asset is checked
over HTTP. A failed post-publish check restores only this release's entry,
never another release's pointer, and leaves previous assets available.
Successful publishes persist a source-linked receipt and rollback index.

Independent acceptance (not full main/API acceptance):

```powershell
python scripts/classic_release.py verify-isolated --expected-main-head <40hex> --expected-api-revision <64hex> --expected-frontend-sha256 <64hex> --approved-frontend-head <40hex>
```

This requires the matching successful release receipt, approved commit on main,
current API/config/schema/slot, served index and health. If the API changes
again, review the new combination rather than silently reusing old proof.
Full verify remains strict; it reports pending backend/administrator source
drift separately. No step drains paid jobs, changes a route, restarts processes
or writes SQLite.
