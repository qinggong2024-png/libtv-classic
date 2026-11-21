"""Account storefront identity is frozen and appended without replacing image prompts."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.api.image_generations import ImageBatchManifest, _expand_manifest
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import Brand, ImageGenerationBatch, User
from libtv_agent.services.brand_identity import append_brand_reference
from test_image_generation_batches import _app, _manifest, _multipart


@pytest.mark.parametrize("assigned", [False, True])
async def test_image_brand_is_frozen_and_preserved_on_replay(tmp_path, assigned):
    app, settings = await _app(tmp_path)
    manifest = _manifest()
    if not assigned:
        manifest["siteProfileId"] = "pagewings"
    original_prompt = _expand_manifest(ImageBatchManifest.model_validate(manifest), 2)[0].prompt
    headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "brand-image"}
    async with app.router.lifespan_context(app):
        async with app.state.database.session_factory() as session:
            user = await session.get(User, uuid.UUID(settings.dev_user_id))
            if assigned:
                if user is None:
                    user = User(id=uuid.UUID(settings.dev_user_id), username="image-brand-user",
                                password_hash="test-only-unused-hash", role=UserRole.USER)
                    session.add(user)
                brand = Brand(name="PW")
                session.add(brand)
                await session.flush()
                user.brand_id = brand.id
                await session.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data, files = _multipart(manifest)
            response = await client.post("/api/v1/image-generation-batches", headers=headers,
                                         data=data, files=files)
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["configuration"]["siteProfileId"] == "pagewings"
            async with app.state.database.session_factory() as session:
                batch = await session.get(ImageGenerationBatch, uuid.UUID(body["id"]))
                assert batch.request_json["cutflowSite"]["profileId"] == "pagewings"
                assert batch.request_json["cutflowSite"]["selection"] == (
                    "account_brand" if assigned else "explicit"
                )
                reference = batch.request_json["cutflowSite"]["brandReference"]
                assert reference["shortName"] == "PW"
                assert app.state.wavespeed_adapter.submissions[0][1]["prompt"] == (
                    append_brand_reference(original_prompt, reference)
                )
                if assigned:
                    brand = await session.get(Brand, user.brand_id)
                    brand.name = "基督"
                    await session.commit()
            data, files = _multipart(manifest)
            repeated = await client.post("/api/v1/image-generation-batches", headers=headers,
                                        data=data, files=files)
            assert repeated.status_code == 201, repeated.text
            assert repeated.json()["id"] == body["id"]
            assert len(app.state.wavespeed_adapter.submissions) == 1
            history = await client.get("/api/v1/image-generation-batches", headers=headers)
            assert history.json()["items"][0]["configuration"]["siteProfileId"] == "pagewings"


async def test_image_cannot_select_another_accounts_brand(tmp_path):
    app, settings = await _app(tmp_path)
    async with app.router.lifespan_context(app):
        async with app.state.database.session_factory() as session:
            brand = Brand(name="泛读书")
            session.add(brand)
            await session.flush()
            user = await session.get(User, uuid.UUID(settings.dev_user_id))
            if user is None:
                user = User(id=uuid.UUID(settings.dev_user_id), username="image-brand-user",
                            password_hash="test-only-unused-hash", role=UserRole.USER)
                session.add(user)
            user.brand_id = brand.id
            await session.commit()
        manifest = {**_manifest(), "siteProfileId": "guidingcross"}
        data, files = _multipart(manifest)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/image-generation-batches",
                headers={"X-User-Id": settings.dev_user_id, "Idempotency-Key": "wrong-brand"},
                data=data, files=files,
            )
            assert response.status_code == 422, response.text
            assert response.json()["detail"]["code"] == "CUTFLOW_SITE_REQUIRED"
            assert app.state.wavespeed_adapter.submissions == []
