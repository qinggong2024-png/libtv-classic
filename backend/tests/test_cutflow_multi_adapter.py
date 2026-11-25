import copy
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from libtv_agent.services.cutflow_handoff import CutflowHandoffError
from libtv_agent.services.cutflow_multi_adapter import (
    CONTRACT_VERSION,
    build_multi_manifest,
    cut_multi_request,
    multi_handoff_key,
    multi_idempotency_key,
    normalize_cut_multi_response,
)
from libtv_agent.services.cutflow_policy import POLICY_VERSION
from libtv_agent.services.cutflow_protocol import (
    LEGACY_JOB_CONTRACT,
    STATUS_SCHEMA,
    check_status_revision,
    remember_status_revision,
)

TASK_ID = uuid.UUID("00000000-0000-4000-8000-000000000011")
MANIFEST_HASH = "a" * 64


def task_fixture():
    return SimpleNamespace(
        id=TASK_ID,
        user_id=uuid.UUID("00000000-0000-4000-8000-000000000010"),
        created_at=datetime(2026, 10, 27, tzinfo=UTC),
        sources_json=[
            {"clipId": "clip-2", "order": 2, "relativePath": "sources/02.mp4",
             "sha256": "b" * 64, "sizeBytes": 20, "durationSeconds": 3.0,
             "userId": "user", "taskId": "task-2", "variantId": "variant-2",
             "artifactId": "artifact-2", "attemptId": "attempt-2",
             "brandIdentity": "tokyo-tiger", "productIdentity": None},
            {"clipId": "clip-1", "order": 1, "relativePath": "sources/01.mp4",
             "sha256": "c" * 64, "sizeBytes": 10, "durationSeconds": 2.0,
             "userId": "user", "taskId": "task-1", "variantId": "variant-1",
             "artifactId": "artifact-1", "attemptId": "attempt-1",
             "brandIdentity": "tokyo-tiger", "productIdentity": None},
        ],
    )


def test_cut_request_includes_manifest_and_contract_identity():
    task = task_fixture()
    manifest = build_multi_manifest(task)
    descriptor = {**manifest, "manifestSha256": MANIFEST_HASH,
                  "manifestRelativePath": f"inbox/multi-{TASK_ID}/manifest.json",
                  "_readyAt": "2026-10-29T12:00:00+00:00",
                  "_statusAuthority": "nas"}

    request = cut_multi_request(task, descriptor)

    assert request["contractVersion"] == CONTRACT_VERSION
    assert request["taskId"] == str(TASK_ID)
    assert request["handoffKey"] == multi_handoff_key(TASK_ID)
    assert request["idempotencyKey"] == multi_idempotency_key(TASK_ID)
    assert request["manifest"]["idempotencyKey"] == multi_idempotency_key(TASK_ID)
    assert request["manifest"]["sources"][0]["clipId"] == "clip-1"
    assert "manifestSha256" not in request["manifest"]
    assert not any(key.startswith("_") for key in request["manifest"])
    assert request["manifestSha256"] == MANIFEST_HASH
    assert request["notificationOnly"] is True
    assert request["statusAuthority"] == "nas-status"
    assert request["statusRelativePath"] == f"outbox/{TASK_ID}/status.json"
    assert request["classicReceiptRelativePath"] == (
        f"receipts/classic/multi-{TASK_ID}.json"
    )


@pytest.mark.parametrize(("field", "value"), [
    ("contractVersion", LEGACY_JOB_CONTRACT),
    ("schema_version", LEGACY_JOB_CONTRACT),
    ("contractVersion", None),
])
def test_cut_request_never_wraps_a_non_v2_manifest(field, value):
    task = task_fixture()
    descriptor = {
        **build_multi_manifest(task),
        "manifestSha256": MANIFEST_HASH,
        "manifestRelativePath": f"inbox/multi-{TASK_ID}/manifest.json",
    }
    descriptor[field] = value

    with pytest.raises(CutflowHandoffError) as error:
        cut_multi_request(task, descriptor)

    assert error.value.code == "CUTFLOW_CONTRACT_UPGRADE_REQUIRED"


def test_manifest_sources_expose_both_field_spellings_in_order():
    """Receiver compatibility: the classic side is the sender, so it cannot be
    tolerant at read time. Each source must carry the documented LAN /1
    snake_case names and CUT's multi-source camelCase names with equal values."""
    manifest = build_multi_manifest(task_fixture())
    first = manifest["sources"][0]

    assert [source["order"] for source in manifest["sources"]] == [1, 2]
    for snake, camel in (
        ("relative_path", "relativePath"),
        ("size_bytes", "size"),
        ("duration_seconds", "durationSeconds"),
    ):
        assert first[snake] == first[camel]
    assert first["relative_path"] == "sources/01.mp4"
    assert first["size_bytes"] == 10
    assert first["duration_seconds"] == 2.0


def test_manifest_reports_provable_brand_binding_and_never_fakes_a_product_id():
    """CUT requires a per-source productId; classic has no product granule.

    The package must therefore expose the identity classic can prove (the
    verified brand) and carry an explicit null productId, so a receiver that
    needs a product identity rejects the package instead of silently mixing two
    products of the same brand into one cut.
    """
    manifest = build_multi_manifest(task_fixture())
    brands = {source["brandIdentity"] for source in manifest["sources"]}
    assert brands == {"tokyotiger"}
    assert all(source["productId"] is None for source in manifest["sources"])

    # The granularity is declared, not inferred: the receiver is told that the
    # binding is brand-level and that no product id accompanies it.
    assert manifest["sourceBinding"] == {
        "kind": "brand", "value": "tokyotiger",
        "granularity": "brand-profile", "productIdProvided": False,
    }

    task = task_fixture()
    task.sources_json[0]["productIdentity"] = "product-42"
    mapped = build_multi_manifest(task)
    assert mapped["sources"][1]["productId"] == "product-42"


def test_manifest_refuses_a_mixed_brand_package():
    """The contract CUT reads must never carry a mixed-brand package."""
    task = task_fixture()
    task.sources_json[0]["brandIdentity"] = "pagewings"
    with pytest.raises(CutflowHandoffError) as error:
        build_multi_manifest(task)
    assert error.value.code == "INCOMPATIBLE_BRANDS"


@pytest.mark.parametrize(("alias", "normalized"), [
    ("TT", "tokyotiger"), ("Tokyo-Tiger", "tokyotiger"),
    ("tokyotiger", "tokyotiger"), ("GC", "guidingcross"),
    ("guidingcross", "guidingcross"), ("NW", "nerdywave"),
    ("nerdywave", "nerdywave"), ("IP", "ironpandafit"),
    ("ironpandafit", "ironpandafit"), ("GK", "geeksoutfit"),
    ("geeksoutfit", "geeksoutfit"), ("TSG", "teachersgram"),
    ("teachersgram", "teachersgram"), ("PPH", "purplehecate"),
    ("purplehecate", "purplehecate"), ("magicstation", "purplehecate"),
    ("PW", "pagewings"), ("pagewings", "pagewings"),
])
def test_business_uuid_is_retained_while_cut_receives_a_recognized_brand(alias, normalized):
    task = task_fixture()
    business_id = "483d70a2-c291-4102-93e5-734166a8767a"
    for source in task.sources_json:
        source.update(
            brandIdentity=business_id,
            siteProfileId=alias,
            brandReference={"profileId": alias, "accountBrandId": business_id},
        )
    before = copy.deepcopy(task.sources_json)

    manifest = build_multi_manifest(task)

    assert task.sources_json == before
    assert manifest["sourceBinding"] == {
        "kind": "brand", "value": normalized, "granularity": "brand-profile",
        "productIdProvided": False, "businessBrandId": business_id,
    }
    assert {item["brandIdentity"] for item in manifest["sources"]} == {normalized}
    assert {item["businessBrandId"] for item in manifest["sources"]} == {business_id}
    assert manifest["taskId"] == str(task.id)
    assert manifest["idempotencyKey"] == multi_idempotency_key(task.id)
    assert all(item["productId"] is None for item in manifest["sources"])


def test_same_normalized_brand_does_not_allow_different_business_owners():
    task = task_fixture()
    for source in task.sources_json:
        source.update(brandIdentity=str(uuid.uuid4()), siteProfileId="tokyotiger")
    with pytest.raises(CutflowHandoffError) as error:
        build_multi_manifest(task)
    assert error.value.code == "INCOMPATIBLE_BRANDS"


@pytest.mark.parametrize("field", ["accountBrandName", "fullName", "shortName"])
def test_brand_reference_names_resolve_uuid_when_profile_is_unavailable(field):
    task = task_fixture()
    business_id = "483d70a2-c291-4102-93e5-734166a8767a"
    for source in task.sources_json:
        source.update(brandIdentity=business_id, brandReference={field: "TT"})
    manifest = build_multi_manifest(task)
    assert manifest["sourceBinding"]["value"] == "tokyotiger"
    assert manifest["sourceBinding"]["businessBrandId"] == business_id


def test_normalized_source_keeps_its_existing_business_brand_id():
    task = task_fixture()
    business_id = "483d70a2-c291-4102-93e5-734166a8767a"
    for source in task.sources_json:
        source.update(brandIdentity="tokyotiger", businessBrandId=business_id)
    manifest = build_multi_manifest(task)
    assert manifest["sourceBinding"]["businessBrandId"] == business_id
    assert all(source["businessBrandId"] == business_id for source in manifest["sources"])


def test_same_business_uuid_cannot_emit_conflicting_cut_brands():
    task = task_fixture()
    for source, profile in zip(task.sources_json, ["tokyotiger", "guidingcross"], strict=True):
        source.update(brandIdentity="483d70a2-c291-4102-93e5-734166a8767a", siteProfileId=profile)
    with pytest.raises(CutflowHandoffError) as error:
        build_multi_manifest(task)
    assert error.value.code == "INCOMPATIBLE_BRANDS"


def test_unresolved_uuid_is_never_sent_as_a_cut_brand():
    task = task_fixture()
    for source in task.sources_json:
        source["brandIdentity"] = "483d70a2-c291-4102-93e5-734166a8767a"
    with pytest.raises(CutflowHandoffError) as error:
        build_multi_manifest(task)
    assert error.value.code == "BRAND_IDENTITY_UNAVAILABLE"


def test_previously_published_manifest_is_not_normalized_or_rekeyed_on_notification():
    task = task_fixture()
    descriptor = {
        **build_multi_manifest(task),
        "manifestSha256": MANIFEST_HASH,
        "manifestRelativePath": f"inbox/multi-{TASK_ID}/manifest.json",
        "_cutBatchId": "existing-batch",
    }
    business_id = "483d70a2-c291-4102-93e5-734166a8767a"
    descriptor["sourceBinding"]["value"] = business_id
    for source in descriptor["sources"]:
        source["brandIdentity"] = business_id
    before = copy.deepcopy(descriptor)

    request = cut_multi_request(task, descriptor)

    assert descriptor == before
    assert request["manifest"]["sourceBinding"]["value"] == business_id
    assert request["manifest"]["sources"] == before["sources"]
    assert request["manifestSha256"] == MANIFEST_HASH
    assert request["taskId"] == str(TASK_ID)


def test_manifest_without_sources_declares_no_binding():
    task = task_fixture()
    task.sources_json = []
    assert build_multi_manifest(task)["sourceBinding"] is None


def test_product_references_are_shipped_in_the_manifest():
    """CUT's quality gate needs the real product images, not a brand-only stand-in."""
    task = task_fixture()
    task.sources_json[0]["productReferences"] = [
        {"refId": "ref-1", "objectKey": "k/1.webp", "sha256": "d" * 64, "sizeBytes": 90,
         "mimeType": "image/jpeg", "width": 800, "height": 800, "filename": "a.webp",
         "relativePath": "references/02/ref-1.webp"},
    ]
    manifest = build_multi_manifest(task)
    assert [item["refId"] for item in manifest["productReferences"]] == ["ref-1"]
    entry = manifest["productReferences"][0]
    assert entry["sourceOrder"] == 2
    assert entry["relativePath"] == entry["relative_path"] == "references/02/ref-1.webp"
    assert entry["size"] == entry["size_bytes"] == 90
    by_order = {source["order"]: source for source in manifest["sources"]}
    assert by_order[2]["productReferenceIds"] == ["ref-1"]
    assert by_order[1]["productReferenceIds"] == []


def test_cut_response_accepts_actual_outer_task_identity_and_maps_completed():
    result = normalize_cut_multi_response({
        "taskId": str(TASK_ID),
        "handoffKey": multi_handoff_key(TASK_ID),
        "manifestSha256": MANIFEST_HASH,
        "status": "completed",
        "batchId": "batch-1",
        "policyReceipt": {
            "state": "accepted", "taskId": str(TASK_ID),
            "handoffKey": multi_handoff_key(TASK_ID),
            "policyVersion": POLICY_VERSION,
            "manifestSha256": MANIFEST_HASH,
        },
        "outputs": [{"outputId": "output-1",
                     "relativePath": f"outbox/{TASK_ID}/output.mp4",
                     "size": 123, "sha256": "d" * 64}],
    }, task_id=TASK_ID, manifest_sha256=MANIFEST_HASH)

    assert result["status"] == "PENDING_REVIEW"
    assert result["cutflowBatchId"] == "batch-1"
    assert result["outputs"] == [{
        "id": "output-1", "relative_path": f"outbox/{TASK_ID}/output.mp4",
        "sha256": "d" * 64, "size_bytes": 123,
    }]


def test_v2_status_requires_schema_and_positive_revision():
    response = {
        "taskId": str(TASK_ID),
        "handoffKey": multi_handoff_key(TASK_ID),
        "manifestSha256": MANIFEST_HASH,
        "status": "processing",
        "policyReceipt": {
            "state": "accepted", "taskId": str(TASK_ID),
            "handoffKey": multi_handoff_key(TASK_ID),
            "policyVersion": POLICY_VERSION,
            "manifestSha256": MANIFEST_HASH,
        },
    }
    with pytest.raises(CutflowHandoffError, match="状态版本"):
        normalize_cut_multi_response(
            response,
            task_id=TASK_ID,
            manifest_sha256=MANIFEST_HASH,
            require_revision=True,
        )
    with pytest.raises(CutflowHandoffError, match="revision"):
        normalize_cut_multi_response(
            {**response, "schemaVersion": STATUS_SCHEMA},
            task_id=TASK_ID,
            manifest_sha256=MANIFEST_HASH,
            require_revision=True,
        )


@pytest.mark.parametrize(
    "mutation", ["task", "handoff", "manifest", "receipt", "receipt_task", "receipt_handoff"],
)
def test_cut_response_rejects_unbound_or_unaccepted_policy(mutation):
    response = {
        "taskId": str(TASK_ID), "handoffKey": multi_handoff_key(TASK_ID),
        "manifestSha256": MANIFEST_HASH, "status": "queued",
        "policyReceipt": {"state": "accepted", "taskId": str(TASK_ID),
                           "handoffKey": multi_handoff_key(TASK_ID),
                           "policyVersion": POLICY_VERSION,
                           "manifestSha256": MANIFEST_HASH},
    }
    if mutation == "task":
        response["taskId"] = str(uuid.uuid4())
    elif mutation == "handoff":
        response["handoffKey"] = "multi-other"
    elif mutation == "manifest":
        response["manifestSha256"] = "e" * 64
    else:
        if mutation == "receipt_task":
            response["policyReceipt"]["taskId"] = str(uuid.uuid4())
        elif mutation == "receipt_handoff":
            response["policyReceipt"]["handoffKey"] = "multi-other"
        else:
            response["policyReceipt"]["policyVersion"] = "unknown"

    with pytest.raises(CutflowHandoffError):
        normalize_cut_multi_response(response, task_id=TASK_ID, manifest_sha256=MANIFEST_HASH)


def test_cut_response_requires_published_outputs_for_completed_status():
    with pytest.raises(CutflowHandoffError, match="没有成片"):
        normalize_cut_multi_response({
            "taskId": str(TASK_ID), "handoffKey": multi_handoff_key(TASK_ID),
            "manifestSha256": MANIFEST_HASH, "status": "completed",
            "policyReceipt": {"state": "accepted", "taskId": str(TASK_ID),
                               "handoffKey": multi_handoff_key(TASK_ID),
                               "policyVersion": POLICY_VERSION,
                               "manifestSha256": MANIFEST_HASH},
            "outputs": [],
        }, task_id=TASK_ID, manifest_sha256=MANIFEST_HASH)


@pytest.mark.parametrize("status", ["queued", "uploading", "editing", "failed", "unknown"])
def test_nonterminal_response_omits_outputs_entirely(status):
    receipt = {"state": "accepted", "taskId": str(TASK_ID),
               "handoffKey": multi_handoff_key(TASK_ID),
               "policyVersion": POLICY_VERSION, "manifestSha256": MANIFEST_HASH}
    result = normalize_cut_multi_response({
        "taskId": str(TASK_ID), "handoffKey": multi_handoff_key(TASK_ID),
        "manifestSha256": MANIFEST_HASH, "status": status, "policyReceipt": receipt,
    }, task_id=TASK_ID, manifest_sha256=MANIFEST_HASH)
    assert result["outputs"] == []


@pytest.mark.parametrize("early_outputs", [
    [{"outputId": "out-early", "relativePath": "outbox/early.mp4",
      "size": 123, "sha256": "x" * 64}],
    "not-yet-a-manifest",
    None,
])
def test_v2_processing_ignores_premature_outputs_without_importing(early_outputs):
    response = {
        "schemaVersion": STATUS_SCHEMA,
        "revision": 3,
        "taskId": str(TASK_ID), "handoffKey": multi_handoff_key(TASK_ID),
        "manifestSha256": MANIFEST_HASH, "status": "processing",
        "policyReceipt": {
            "state": "accepted", "taskId": str(TASK_ID),
            "handoffKey": multi_handoff_key(TASK_ID),
            "policyVersion": POLICY_VERSION,
            "manifestSha256": MANIFEST_HASH,
        },
        "outputs": early_outputs,
    }
    result = normalize_cut_multi_response(
        response, task_id=TASK_ID, manifest_sha256=MANIFEST_HASH,
        require_revision=True,
    )
    assert result["status"] == "EDITING"
    assert result["outputs"] == []
    assert result["revision"] == 3
    # No sanitized snapshot: a same-revision output change must still be detected.
    first = check_status_revision({}, response, required=True)
    remembered = remember_status_revision({}, first)
    assert result["snapshotSha256"] == first.fingerprint
    with pytest.raises(CutflowHandoffError, match="相同 revision"):
        check_status_revision(remembered, {**response, "outputs": []}, required=True)


def test_other_nonterminal_states_still_reject_early_outputs():
    for status in ("accepted", "failed", "unknown"):
        with pytest.raises(CutflowHandoffError, match="未完成状态不得包含成片字段"):
            normalize_cut_multi_response({
                "taskId": str(TASK_ID), "handoffKey": multi_handoff_key(TASK_ID),
                "manifestSha256": MANIFEST_HASH, "status": status,
                "policyReceipt": {
                    "state": "accepted", "taskId": str(TASK_ID),
                    "handoffKey": multi_handoff_key(TASK_ID),
                    "policyVersion": POLICY_VERSION,
                    "manifestSha256": MANIFEST_HASH,
                },
                "outputs": [{"outputId": "early"}],
            }, task_id=TASK_ID, manifest_sha256=MANIFEST_HASH)


def test_non_dict_policy_receipt_fails_closed_without_attribute_error():
    with pytest.raises(CutflowHandoffError):
        normalize_cut_multi_response({
            "taskId": str(TASK_ID), "handoffKey": multi_handoff_key(TASK_ID),
            "manifestSha256": MANIFEST_HASH, "status": "queued", "policyReceipt": "invalid",
        }, task_id=TASK_ID, manifest_sha256=MANIFEST_HASH)
