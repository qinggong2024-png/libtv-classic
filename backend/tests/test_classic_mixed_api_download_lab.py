import asyncio
import subprocess
import types

from libtv_agent.db import Database
from libtv_agent.services.cutflow_downloads import download_filename
from test_cutflow_downloads import handoff_env as handoff_env
from test_cutflow_downloads import multi, output
from test_cutflow_downloads import naming_env as naming_env


async def test_exact_old_new_api_share_download_ledger(naming_env):
    database, *_ = naming_env
    source = await asyncio.to_thread(subprocess.check_output, [
        "git", "show", "6c99d24:backend/src/libtv_agent/services/cutflow_downloads.py",
    ], text=True, encoding="utf-8")
    old = types.ModuleType("old_classic_download_names")
    exec(compile(source, "old_classic_download_names.py", "exec"), old.__dict__)
    other = Database(str(database.engine.url))
    row = multi()
    async def name(i, identifier):
        factory = database.session_factory if i % 2 else other.download_metadata_session_factory
        fn = old.download_filename if i % 2 else download_filename
        async with factory() as session:
            return await fn(session, row, output(identifier))
    try:
        same = await asyncio.gather(*(name(i, "same") for i in range(8)))
        assert set(same) == {"PMO-TT261114AA01.mp4"}
        distinct = await asyncio.gather(*(name(i, f"different-{i}") for i in range(8)))
        assert set(distinct) == {f"PMO-TT261114AA{i:02d}.mp4" for i in range(2, 10)}
    finally:
        await other.dispose()
