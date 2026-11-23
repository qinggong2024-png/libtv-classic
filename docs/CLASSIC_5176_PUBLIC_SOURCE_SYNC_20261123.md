# ALLWE Classic 5176 — safe public source sync (2026-11-23)

## Source of truth

- Running Classic site: `http://192.168.30.126:5176/`, backend `127.0.0.1:8002`.
- Local production source baseline: `E:\Projects\libtv-classic-0e3d1c8-main`, commit `68fc5b57644c3892be590549bd90c0fb4dbd2bf1`.
- Public GitHub base: `qinggong2024-png/libtv-classic` main at `33dac54a03ada271534576eba3c061de21d36ba0`.
- The Git histories have no common merge base. This is a **source-tree alignment commit** on top of the public GitHub history, not a Git merge of the two roots.

## Scope

Sync source code and directly related regression tests for:
- `backend/src`, `backend/alembic`, `backend/scripts`, `backend/knowledge`, `backend/tests`
- `frontend/src`
- `scripts`
- Runtime dependency manifests only when different.

Before this document, 104 staged changes were identified (53 added, 51 modified, 8,422 insertions, 147 deletions). Files are taken verbatim from the running Classic working tree; no production code is redesigned for this sync.

Excluded on purpose: `var/`, databases, media, artifacts, `.env*`, tokens, passwords, secret files, local user profiles, historical design drafts and unrelated documentation. Scans of the source diff for key/token patterns yielded zero candidate secrets.

## Release safety

- This branch MUST NOT be force-pushed to `main`.
- Deploying GitHub to the 5176 host is NOT part of this sync; 5176 remains authoritative until runtime checks and an explicit deployment gate.
- Keep the remote patch in a review branch/PR first; retain existing main and production intact.
- Before merge, verify smoke tests, database migrations, frontend build and significant OOTD runtime regressions; the legacy `test_ootd_integration.py` assertions may not match current dynamic multi-image flows and require independent baseline confirmation.
- Recheck `main` head and resolve concurrent updates prior to merge; no hidden overwrite or deletion.

## CUT scope

The one-surviving-original CUT salvage functions are byte-identical on local and public branches and do not need a second cherry-pick. The CUT charge lifecycle repair is a separate isolated change and is **not** implicitly deployed with this source sync.
