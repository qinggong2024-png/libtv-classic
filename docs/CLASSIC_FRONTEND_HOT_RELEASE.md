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
