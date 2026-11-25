"""No production IO or paid provider calls in candidate release tests."""
import json
import subprocess
import sys
from pathlib import Path
from contextlib import nullcontext

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import classic_api_candidate as api
import classic_release_manager as manager
import classic_green_operator as operator
from test_classic_release_manager import api_pins, api_state


@pytest.fixture
def candidate_case(tmp_path, monkeypatch):
    root = tmp_path / "main"
    candidate = tmp_path / "candidate"
    root.mkdir()
    candidate.mkdir()
    pins = api_pins()
    base, head = "a"*40, "b"*40
    state = api_state(pins)
    state["standbyApiPort"] = pins["target"]
    state["mainCheckout"] = {"gitHead": base, "matchesRunning": True}
    state["userUrl"] = "http://10.12.34.56:5176/"
    changes = ["backend/src/libtv_agent/api/admin.py"]
    def git(path, *args):
        if args == ("branch", "--show-current"): return "main"
        if args == ("rev-parse", "HEAD"): return base if path == root else head
        if args[0] == "rev-parse": return "common"
        if args[0] == "merge-base": return ""
        if args[0] == "diff": return "\n".join(changes)
        raise AssertionError(args)
    monkeypatch.setattr(api.release, "git", git)
    monkeypatch.setattr(api, "clean", lambda _: True)
    monkeypatch.setattr(api, "workspace_source_revision", lambda _: pins["target_revision"])
    monkeypatch.setattr(api.release, "report", lambda _: state)
    monkeypatch.setattr(api, "reviewed_pair", lambda a,b: {a,b} == {pins["active"],pins["target_revision"]})
    monkeypatch.setattr(api, "http_digest", lambda: pins["frontend"])
    monkeypatch.setattr(api, "tree", lambda *a: "frontend-tree")
    monkeypatch.setattr(api, "live_frontend_baseline", lambda *a: {"base": base})
    args = dict(expected_main=base, expected_candidate=head, expected_api=pins["target_revision"], expected_standby=pins["active"])
    return root,candidate,pins,state,changes,args


def test_preview_no_writes_and_scope_and_other_candidate_block(candidate_case, monkeypatch):
    root,candidate,pins,state,changes,args = candidate_case
    monkeypatch.setattr(manager, "store_intent", lambda *a: pytest.fail("preview cannot queue"))
    assert api.deploy(root,candidate,**args)["ready"]
    assert not api.deploy(root,candidate,**args,apply=True)["ready"]
    changes.append("frontend/src/App.tsx")
    assert api.plan(root,candidate,**args)["blockers"] == ["API_ONLY_CANDIDATE_SCOPE_REQUIRED"]
    changes.pop()
    state["standbyApiSourceRevision"] = "f"*64
    assert api.plan(root,candidate,**args)["blockers"] == ["ANOTHER_STANDBY_CANDIDATE_PRESENT"]
    state["standbyApiSourceRevision"] = None
    assert api.plan(root,candidate,**args)["prepare"]


def test_queue_candidate_requires_full_acceptance(candidate_case, monkeypatch):
    root,candidate,pins,state,changes,args = candidate_case
    monkeypatch.setattr(api, "prepare", lambda *a: None)
    monkeypatch.setattr(manager, "switch_reviewed_api", lambda *a, **kw: {
        "ready": True, "apiSwitched": True, "switchReceipt": {"action":"switched"},
        "compatibilityReceipt":"receipt.json", "fullReleaseVerified":True})
    result = api.deploy(root,candidate,**args,apply=True,reviewed=True)
    assert result["ready"] and result["outcome"]["state"] == "verified"
    assert manager.status(root)[0]["state"] == "verified"


def test_uncertain_promotion_blocks_later_jobs(candidate_case, monkeypatch):
    root,candidate,pins,state,changes,args = candidate_case
    monkeypatch.setattr(api, "prepare", lambda *a: None)
    def fail(*a,**kw): raise RuntimeError("main changed after switch")
    monkeypatch.setattr(manager, "switch_reviewed_api", fail)
    result = api.deploy(root,candidate,**args,apply=True,reviewed=True)
    assert result["outcome"]["state"] == "needs-review"
    assert manager.run_next(root)["state"] == "idle-or-blocked"


@pytest.mark.parametrize("target",[8002,8003])
def test_prepare_uses_exact_independent_slot(candidate_case, monkeypatch,target):
    import classic_green_api as green
    root,candidate,pins,state,changes,args = candidate_case
    pins["target"],pins["current"] = target,16005-target
    state.update(activeApiPort=pins["current"],standbyApiSourceRevision=pins["active"])
    seen=[]
    monkeypatch.setattr(operator,"needs_independent",lambda: True)
    monkeypatch.setattr(operator,"dispatch",lambda argv: seen.append(argv))
    monkeypatch.setattr(green,"replace_green_online",lambda *a,**kw: seen.append((a,kw)))
    api.prepare(root,candidate,pins,pins["active"])
    if target == 8002:
        assert seen[0][0] == "prepare-blue"
        assert pins["target_revision"] in seen[0]
    else:
        assert seen[0][1]["expected_standby"] is None


def test_switch_then_promote_then_receipt(candidate_case, monkeypatch):
    root,candidate,pins,state,changes,args = candidate_case
    base,head = args["expected_main"],args["expected_candidate"]
    after = api_state(pins,target=True)
    after["mainCheckout"] = {"gitHead":base,"matchesRunning":False}
    after["userUrl"] = state["userUrl"]
    after["standbyApiPort"] = pins["current"]
    snapshots = iter([state,after,after])
    monkeypatch.setattr(manager,"report",lambda _:next(snapshots))
    monkeypatch.setattr(manager,"http_digest",lambda:pins["frontend"])
    monkeypatch.setattr(manager,"live_frontend_baseline",lambda *a:{"base":base,"artifactSourceHead":base,"receipt":"prior.json"})
    monkeypatch.setattr(manager,"tree",lambda *a:"same")
    monkeypatch.setattr(manager,"clean",lambda _:True)
    monkeypatch.setattr(manager,"release_lock",lambda _:nullcontext())
    monkeypatch.setattr(manager,"route_activation_lock",lambda _:nullcontext())
    monkeypatch.setattr(manager.shutil,"which",lambda _:"node-test")
    import classic_active_release_status as active
    monkeypatch.setattr(active,"workspace_source_revision",lambda _:pins["target_revision"])
    monkeypatch.setattr(api,"plan",lambda *a,**k:{"ready":True,"pins":pins,"prepare":False})
    events=[]
    def git(path,*argv):
        if argv[0]=="merge":
            events.append("promote")
            after["mainCheckout"]={"gitHead":head,"matchesRunning":True}
        if argv == ("rev-parse","HEAD"):
            return base if path==root else head
        return ""
    monkeypatch.setattr(manager,"git",git)
    def run(cmd,**kw):
        events.append("switch" if "--apply" in cmd else "preview")
        return subprocess.CompletedProcess(cmd,0,json.dumps({"action":"switched","from":8002,"to":8003}),"")
    monkeypatch.setattr(manager.subprocess,"run",run)
    def receipt(*a,**kw):
        events.append("receipt")
        return "verified.json"
    monkeypatch.setattr(manager,"record_receipt",receipt)
    intent={"candidate":str(candidate),"main":base,"candidate_head":head,"previous_standby":pins["active"]}
    result=manager.switch_reviewed_api(root,pins,candidate_delivery=intent)
    assert result["fullReleaseVerified"] and result["mainApiMatches"]
    assert events==["preview","switch","promote","receipt"]
