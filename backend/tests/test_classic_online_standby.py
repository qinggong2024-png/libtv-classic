import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from classic_online_standby import ACTIVE_WORK, assess_preparation  # noqa: E402

REV = "a" * 64
DEPLOYMENT = "libtv-classic-lan-0e3d1c8"


@pytest.fixture(autouse=True)
def independent_test_context(monkeypatch):
    import classic_green_operator as operator
    monkeypatch.setattr(operator, "needs_independent", lambda: False)


def evidence(target):
    active = 8003 if target == 8002 else 8002
    route = dict(activeApiPort=active, hotRouteVersion=1, configured=True,
                 allowedApiPorts=[8002, 8003], drainContractVersion=1,
                 pendingRouteSelections=0, inFlightByPort={"8002": 0, "8003": 0})
    info = dict(sourceRevision=REV, configRevision="same-config", schemaRevision="same-schema")
    gate = dict(gateVersion=1, deploymentId=DEPLOYMENT, configured=True,
                newVideoAdmissionsPaused=False)
    durable = dict(contractVersion=1, deploymentId=DEPLOYMENT, analysis="READY",
                   confirmation="READY", configRevision="durable-config")
    drain = dict(contractVersion=1, recoveryLockVersion=1, deploymentId=DEPLOYMENT,
                 sourceRevision=REV, durableAnalysis=True, durableConfirmation=True,
                 workCounts={k: 0 for k in ("analysis_jobs", "confirmation_jobs",
                             "subject_element_jobs", "feedback_learning_jobs",
                             "http", "recovery", "imageRefresh", "login")})
    return [route, info, dict(info), gate, dict(gate), durable, dict(durable),
            dict(drain, port=active, ownsRecovery=True, retirementReady=False),
            dict(drain, port=target, ownsRecovery=False, retirementReady=True),
            dict(blockers=sorted(ACTIVE_WORK), counts={"pendingPaidCommands": 4})]


@pytest.mark.parametrize("target", [8002, 8003])
def test_prepare_only_does_not_require_cloud_or_cut_quiescence(target):
    values = evidence(target)
    result = assess_preparation(*values, target=target, expected_active=REV)
    assert result["readyToPrepareStandby"]
    assert set(result["informationalActiveWork"]) == ACTIVE_WORK
    assert not result["permitsRouteSwitch"] and not result["permitsWorkerHandoff"]


@pytest.mark.parametrize("index,key,value", [
    (0, "pendingRouteSelections", 1), (0, "activeApiPort", 8002),
    (2, "configRevision", "different"), (2, "schemaRevision", "different"),
    (4, "newVideoAdmissionsPaused", True), (5, "confirmation", "RUNNER_NOT_READY"),
    (6, "configRevision", "different"), (7, "recoveryLockVersion", 0),
    (8, "retirementReady", False), (8, "ownsRecovery", True),
    (9, "blockers", ["PAID_UNKNOWN_INCREASED"]),
    (9, "blockers", ["UNRECOGNIZED_SAFETY_BLOCKER"]),
])
def test_online_prepare_fails_closed(index, key, value):
    values = copy.deepcopy(evidence(8002))
    values[index][key] = value
    assert not assess_preparation(*values, target=8002,
                                  expected_active=REV)["readyToPrepareStandby"]


def test_green_online_preparation_uses_existing_slot_lock_and_drain(tmp_path, monkeypatch):
    import classic_blue_api as blue
    import classic_green_api as green
    import classic_online_standby as standby

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    release = tmp_path / "candidate"
    release.mkdir()
    active = dict(sourceRevision=REV, configRevision="config", schemaRevision="schema")
    events = []
    monkeypatch.setattr(green, "STORAGE", tmp_path)
    monkeypatch.setattr(green, "validated_release", lambda path, _: path)
    monkeypatch.setattr(blue, "supervisor_supports_blue", lambda _: True)
    monkeypatch.setattr(standby, "guard", lambda *_: (active, active))
    monkeypatch.setattr(green, "read_manifest", lambda: {"sourceRevision": REV})
    monkeypatch.setattr(green, "green_process", lambda _: object())
    monkeypatch.setattr(green, "_stop_green_locked", lambda: events.append("stop-standby"))

    def start(path, revision, **kwargs):
        assert path == release and revision == "b" * 64
        assert kwargs == {"online_prepare": True}
        assert (runtime / "api-routing.lock").exists()
        events.append("start-standby")

    monkeypatch.setattr(green, "_start_green_locked", start)
    monkeypatch.setattr(standby, "verify_prepared", lambda *_: events.append("verify"))
    result = green.replace_green_online(release, "b" * 64, expected_active=REV)
    assert result["action"] == "dry-run" and not events
    result = green.replace_green_online(release, "b" * 64, expected_active=REV, apply=True)
    assert result["action"] == "standby-prepared"
    assert events == ["stop-standby", "start-standby", "verify"]
    assert not (runtime / "api-routing.lock").exists()


def test_joint_plan_reports_runner_pin_transition_without_mutation(tmp_path):
    import json

    from classic_joint_release import runner_transition

    path = tmp_path / "var/classic-lan/runtime/durable-execution.json"
    path.parent.mkdir(parents=True)
    original = json.dumps({"version": 1, "analysis": True, "confirmation": True,
                           "analysisConcurrency": 4, "runnerSourceRevision": REV})
    path.write_text(original, encoding="utf-8")
    assert runner_transition(tmp_path, REV)["readyForRunnerReplacement"]
    result = runner_transition(tmp_path, "b" * 64)
    assert result["state"] == "RUNNER_PIN_TRANSITION_REQUIRED"
    assert not result["readyForRunnerReplacement"]
    assert path.read_text(encoding="utf-8") == original


def test_joint_plan_uses_effective_runner_pin(tmp_path, monkeypatch):
    import json
    import classic_worker_source
    from classic_joint_release import runner_transition

    runtime = tmp_path / "var/classic-lan/runtime"
    runtime.mkdir(parents=True)
    (runtime / "durable-execution.json").write_text(json.dumps({
        "version": 1, "analysis": True, "confirmation": True,
        "analysisConcurrency": 4, "runnerSourceRevision": REV,
    }), encoding="utf-8")
    (runtime / "worker-runner-pin.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(classic_worker_source, "worker_source",
                        lambda *args: (tmp_path, "b" * 64))
    result = runner_transition(tmp_path, "b" * 64)
    assert result["currentRunnerSourceRevision"] == "b" * 64
    assert result["configuredRunnerBaseline"] == REV
    assert result["readyForRunnerReplacement"]


def test_joint_plan_distinct_standby_and_unreviewed_pair(tmp_path, monkeypatch):
    import classic_joint_release as joint
    target, old = "b" * 64, "c" * 64
    monkeypatch.setattr(joint, "strict_plan", lambda *a, **kw: {
        "activeRevision": REV, "targetPort": 8003, "targetAlreadyCompatible": False,
    })
    monkeypatch.setattr(joint, "workspace_source_revision", lambda _: target)
    monkeypatch.setattr(joint, "git_head", lambda _: "1" * 40)
    calls = []
    def inspect(*args, **kwargs):
        calls.append(kwargs["expected_standby"])
        return {"readyToPrepareStandby": True, "blockers": []}, {}, {}
    monkeypatch.setattr(joint, "standby_plan", inspect)
    monkeypatch.setattr(joint, "reviewed_pair", lambda a, b: b == old)
    result = joint.plan(tmp_path, REV, root=tmp_path, expected_standby=old)
    assert calls == [old]
    assert result["standbyPreparation"]["blockers"] == ["REVIEWED_EXACT_SOURCE_PAIR_REQUIRED"]
    assert not result["standbyPreparation"]["readyToPrepareStandby"]
    monkeypatch.setattr(joint, "reviewed_pair", lambda *args: True)
    assert joint.plan(tmp_path, REV, root=tmp_path, expected_standby=old)[
        "standbyPreparation"]["readyToPrepareStandby"]


def test_explicit_standby_revision_does_not_relax_identity():
    values = evidence(8002)
    target = "b" * 64
    values[2]["sourceRevision"] = target
    values[8]["sourceRevision"] = target
    assert not assess_preparation(*values, target=8002,
                                  expected_active=REV)["readyToPrepareStandby"]
    assert assess_preparation(*values, target=8002, expected_active=REV,
                               expected_standby=target)["readyToPrepareStandby"]
    values[8]["sourceRevision"] = REV
    assert not assess_preparation(*values, target=8002, expected_active=REV,
                                   expected_standby=target)["readyToPrepareStandby"]


def test_unreviewed_source_pair_cannot_activate(monkeypatch, tmp_path):
    import classic_online_standby as module

    def forbidden():
        pytest.fail("Unreviewed source pair must fail before runtime inspection")

    monkeypatch.setattr(module, "web_route", forbidden)
    with pytest.raises(RuntimeError, match="reviewed exact source pair"):
        module.verify_api_only_switch(
            tmp_path, current=8003, target=8002,
            expected_active=REV, expected_target="b" * 64,
        )


def test_image_materialization_pair_is_exact_and_symmetric():
    from classic_online_standby import IMAGE_API_ONLY_PAIR, reviewed_pair

    first, second = sorted(IMAGE_API_ONLY_PAIR)
    assert reviewed_pair(first, second) and reviewed_pair(second, first)
    assert not reviewed_pair(first, "e" * 64)


def test_runner_compatibility_is_not_a_general_protected_contract_exception():
    from classic_online_standby import RUNNER_COMPAT_API_PAIR, reviewed_pair

    first, second = sorted(RUNNER_COMPAT_API_PAIR)
    assert reviewed_pair(first, second) and reviewed_pair(second, first)
    assert not reviewed_pair(first, "e" * 64)


def test_upload_transaction_pair_is_exact_and_reversible():
    from classic_release_classifier import UPLOAD_TX_API_PAIR, reviewed_pair, reviewed_api_path

    first, second = sorted(UPLOAD_TX_API_PAIR)
    assert reviewed_pair(first, second) and reviewed_pair(second, first)
    assert reviewed_api_path(first, second) == [first, second]
    assert not reviewed_pair(first, "e" * 64)
    assert reviewed_api_path(first, "e" * 64) is None

def test_reliability_admission_api_pair_exact_and_reversible():
    from classic_release_classifier import RELIABILITY_ADMISSION_API_PAIR, reviewed_pair, reviewed_api_path

    first, second = sorted(RELIABILITY_ADMISSION_API_PAIR)
    assert reviewed_pair(first, second) and reviewed_pair(second, first)
    assert reviewed_api_path(first, second) == [first, second]
    assert not reviewed_pair(first, "e" * 64)
    assert reviewed_api_path(first, "e" * 64) is None



def test_lossless_sol_api_pair_exact_and_reversible():
    from classic_release_classifier import SOL_JPEG_API_PAIR, reviewed_pair, reviewed_api_path

    first, second = sorted(SOL_JPEG_API_PAIR)
    assert reviewed_pair(first, second)
    assert reviewed_pair(second, first)
    assert reviewed_api_path(first, second) == [first, second]
    assert not reviewed_pair(first, "f" * 64)


def test_sol400_api_pair_exact_and_reversible():
    from classic_release_classifier import SOL400_API_PAIR, reviewed_pair, reviewed_api_path
    first, second = sorted(SOL400_API_PAIR)
    assert reviewed_pair(first, second) and reviewed_pair(second, first)
    assert reviewed_api_path(first, second) == [first, second]
    assert not reviewed_pair(first, "e" * 64)


def test_restore_vacant_green_requires_exact_dead_manifest_and_keeps_route(tmp_path, monkeypatch):
    import json
    import classic_blue_api as blue
    import classic_green_api as green
    import classic_online_standby as standby
    import classic_release as release_module
    import classic_v2_canary_preflight as preflight
    from contextlib import nullcontext
    from classic_release_classifier import SOL400_API_PAIR
    old, new = sorted(SOL400_API_PAIR)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    manifest = runtime / "green-api.json"
    previous = {"version": 1, "pid": 123, "releaseRoot": "old", "sourceRevision": "c" * 64}
    manifest.write_text(json.dumps(previous))
    values = evidence(8003)
    values[1]["sourceRevision"] = old
    values[7]["sourceRevision"] = old
    monkeypatch.setattr(green, "STORAGE", tmp_path)
    monkeypatch.setattr(green, "MANIFEST", manifest)
    monkeypatch.setattr(green, "validated_release", lambda path, _: path)
    monkeypatch.setattr(blue, "supervisor_supports_blue", lambda _: True)
    monkeypatch.setattr(standby, "verify_candidate", lambda *_: None)
    monkeypatch.setattr(release_module, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(green, "web_route", lambda: values[0])
    monkeypatch.setattr(preflight, "check", lambda *a, **k: values[9])
    monkeypatch.setattr(green, "json_get", lambda port, path: {
        "status": values[1], "classic-new-video-admission": values[3],
        "classic-durable-execution": values[5], "classic-api-drain": values[7],
    }[path.rsplit("/", 1)[-1]])
    class Probe:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def connect_ex(self, addr): return 10061 if green.os.name == "nt" else 111
    monkeypatch.setattr(green.socket, "socket", Probe)
    args = dict(expected_active=old, expected_previous=previous["sourceRevision"])
    monkeypatch.setattr(green, "green_process", lambda _: object())
    with pytest.raises(RuntimeError, match="not verified vacant"):
        green.restore_vacant_green_online(tmp_path, new, **args)
    monkeypatch.setattr(green, "green_process", lambda _: None)
    assert green.restore_vacant_green_online(tmp_path, new, **args)["action"] == "dry-run"
    assert json.loads(manifest.read_text()) == previous
    events = []
    def start(*args, **kwargs):
        assert not manifest.exists() and (runtime / "api-routing.lock").exists()
        events.append("start")
    monkeypatch.setattr(green, "_start_green_locked", start)
    monkeypatch.setattr(standby, "verify_prepared", lambda *a: events.append("verify"))
    result = green.restore_vacant_green_online(tmp_path, new, apply=True, **args)
    assert result["action"] == "vacant-standby-prepared" and events == ["start", "verify"]
    assert json.loads(Path(result["previousManifest"]).read_text()) == previous
    assert values[0]["activeApiPort"] == 8002


def test_sol_stream_api_pair_exact_only():
    from classic_release_classifier import SOL_STREAM_API_PAIR, reviewed_pair, reviewed_api_path
    first, second = sorted(SOL_STREAM_API_PAIR)
    assert reviewed_pair(first, second) and reviewed_pair(second, first)
    assert reviewed_api_path(first, second) == [first, second]
    assert not reviewed_pair(first, "f" * 64)
