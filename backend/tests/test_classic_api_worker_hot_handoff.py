"""Isolated fail-closed hot API role and OS worker exclusivity tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from libtv_agent.config import Settings
from libtv_agent.services.classic_api_active_role import api_owns_background_recovery

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from classic_worker_lease import ClassicWorkerAlreadyRunning, exclusive_classic_worker  # noqa: E402


def _settings(port: int, file: Path | None) -> Settings:
    return Settings(
        _env_file=None, app_env="test", app_port=port,
        classic_api_route_file=file,
    )


def test_unconfigured_nonclassic_api_keeps_existing_recovery_behavior():
    assert api_owns_background_recovery(_settings(8000, None))


def test_classic_blue_green_background_ownership_changes_only_after_atomic_route(tmp_path):
    route = tmp_path / "route.json"
    blue = _settings(8002, route)
    green = _settings(8003, route)
    assert api_owns_background_recovery(blue)
    assert not api_owns_background_recovery(green)
    route.write_text(json.dumps({"version": 1, "activeApiPort": 8003}), encoding="utf-8")
    assert not api_owns_background_recovery(blue)
    assert api_owns_background_recovery(green)
    route.write_text(json.dumps({"version": 1, "activeApiPort": 8002}), encoding="utf-8")
    assert api_owns_background_recovery(blue)
    assert not api_owns_background_recovery(green)


@pytest.mark.parametrize("bad", [
    "{broken", "[]", "{}", '{"version":2,"activeApiPort":8002}',
    '{"version":1,"activeApiPort":8004}',
    '{"version":true,"activeApiPort":8002}',
    '{"version":1,"activeApiPort":8002,"host":"evil"}',
])
def test_corrupt_route_disables_recovery_on_both_processes(tmp_path, bad):
    route = tmp_path / "route.json"
    route.write_text(bad, encoding="utf-8")
    assert not api_owns_background_recovery(_settings(8002, route))
    assert not api_owns_background_recovery(_settings(8003, route))


def test_runner_and_cutflow_are_separately_exclusive_even_across_processes(tmp_path):
    directory = tmp_path.resolve()
    script = (
        "from pathlib import Path; import sys; "
        f"sys.path.insert(0, {str(SCRIPTS)!r}); "
        "from classic_worker_lease import exclusive_classic_worker; "
        "with_lock=exclusive_classic_worker(sys.argv[1],Path(sys.argv[2])); "
        "with_lock.__enter__(); with_lock.__exit__(None,None,None)"
    )
    for kind in ("runner", "cutflow"):
        with exclusive_classic_worker(kind, directory):
            with pytest.raises(ClassicWorkerAlreadyRunning):
                with exclusive_classic_worker(kind, directory):
                    pass
            attempted = subprocess.run(
                [sys.executable, "-c", script, kind, str(directory)],
                capture_output=True, text=True, timeout=10,
            )
            assert attempted.returncode != 0
            assert "already has an exclusive worker" in attempted.stderr
        recovered = subprocess.run(
            [sys.executable, "-c", script, kind, str(directory)],
            capture_output=True, text=True, timeout=10,
        )
        assert recovered.returncode == 0, recovered.stderr
    with exclusive_classic_worker("runner", directory):
        with exclusive_classic_worker("cutflow", directory):
            pass


@pytest.mark.asyncio
async def test_green_api_standby_lifespan_does_not_recover_or_submit_original_tasks(
    tmp_path, monkeypatch,
):
    import importlib

    api_module = importlib.import_module("libtv_agent.app")
    route = tmp_path / "routing.json"
    settings = Settings(
        _env_file=None, app_env="test", app_port=8003,
        classic_api_route_file=route,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'standby.db').as_posix()}",
        artifact_root=tmp_path / "artifacts",
        object_store_mode="local", runner_root=tmp_path / "runner",
        ai_adapter_mode="fake", kling_cli_enabled=False,
    )
    calls = []

    async def would_be_unsafe(*_args, **_kwargs):
        calls.append("original paid/background task")
        return 0

    for name in (
        "_recover_stale_analyses",
        "_recover_stale_materialization_tasks",
        "_recover_stale_confirmation_claims",
        "recover_subject_element_jobs",
        "recover_feedback_learning_jobs",
        "_refresh_active_image_generation_batches",
    ):
        monkeypatch.setattr(api_module, name, would_be_unsafe)
    app = api_module.create_app(settings)
    async with app.router.lifespan_context(app):
        assert not calls
    assert not calls
