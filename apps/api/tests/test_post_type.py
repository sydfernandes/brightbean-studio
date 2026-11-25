"""post_type on create, update, and schedule — REST, OpenAPI, and MCP."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.api_keys import services
from apps.composer.models import PlatformPost, Post
from apps.media_library.models import MediaAsset
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership

MCP_URL = "/api/v1/mcp/"


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="stories@example.com",
        password="testpass123",
        name="Stories",
        tos_accepted_at=timezone.now(),
    )


@pytest.fixture
def organization(db):
    from apps.organizations.models import Organization

    return Organization.objects.create(name="Stories Org")


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Stories WS", organization=organization)


@pytest.fixture
def owner(db, user, organization, workspace):
    OrgMembership.objects.create(user=user, organization=organization, org_role=OrgMembership.OrgRole.OWNER)
    return WorkspaceMembership.objects.create(
        user=user, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )


def _account(workspace, platform, platform_id):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform=platform,
        account_platform_id=platform_id,
        account_name=platform,
        connection_status="connected",
    )


def _asset(workspace, media_type, name):
    return MediaAsset.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        file=f"test/{name}",
        filename=name,
        media_type=media_type,
        mime_type="video/mp4" if media_type == MediaAsset.MediaType.VIDEO else "image/jpeg",
    )


def _client(user, workspace, accounts):
    key = services.issue_api_key(
        workspace=workspace,
        social_accounts=accounts,
        issued_by=user,
        name="stories",
        permissions=list(PERMISSION_KEYS),
    )
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")


def _when():
    return (timezone.now() + timedelta(hours=2)).isoformat().replace("+00:00", "Z")


def _enum_values(prop):
    if "enum" in prop:
        return set(prop["enum"])
    for option in prop.get("anyOf", []):
        if "enum" in option:
            return set(option["enum"])
    return set()


def test_openapi_schema_documents_post_type():
    from apps.api.api import api

    schema = api.get_openapi_schema()
    components = schema["components"]["schemas"]
    for name in ("CreatePostRequest", "UpdatePostRequest", "ScheduleRequest"):
        prop = components[name]["properties"]["post_type"]
        assert _enum_values(prop) == {"feed", "reel", "story"}
        assert "story" in prop["description"]
        assert "Instagram" in prop["description"]
    summary = components["PlatformPostSummary"]["properties"]["post_type"]
    assert "feed" in summary["description"]
    description = schema["info"]["description"]
    assert "post_type" in description
    assert "video_stories" in description


def test_mcp_tools_advertise_post_type():
    from apps.mcp.tools import get_tool

    for name in ("create_draft", "schedule_post", "schedule_draft"):
        prop = get_tool(name).input_schema["properties"]["post_type"]
        assert prop["enum"] == ["feed", "reel", "story"]


@pytest.mark.django_db
class TestPostTypeApi:
    def test_schedule_instagram_story_and_read_it_back(self, user, owner, workspace):
        account = _account(workspace, "instagram", "ig-1")
        image = _asset(workspace, MediaAsset.MediaType.IMAGE, "story.jpg")
        client = _client(user, workspace, [account])

        response = client.post(
            "/api/v1/posts/",
            data=json.dumps(
                {
                    "social_account_id": str(account.id),
                    "caption": "Behind the scenes",
                    "media_asset_ids": [str(image.id)],
                    "post_type": "story",
                    "action": "schedule",
                    "scheduled_at": _when(),
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["platform_posts"][0]["post_type"] == "story"
        assert body["platform_posts"][0]["status"] == "scheduled"
        stored = PlatformPost.objects.get(post_id=body["id"])
        assert stored.platform_extra["post_type"] == "story"

    def test_schedule_facebook_video_story(self, user, owner, workspace):
        account = _account(workspace, "facebook", "page-1")
        video = _asset(workspace, MediaAsset.MediaType.VIDEO, "story.mp4")
        client = _client(user, workspace, [account])

        response = client.post(
            "/api/v1/posts/",
            data=json.dumps(
                {
                    "social_account_id": str(account.id),
                    "caption": "Page story",
                    "media_asset_ids": [str(video.id)],
                    "post_type": "story",
                    "action": "schedule",
                    "scheduled_at": _when(),
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 201, response.content
        assert response.json()["platform_posts"][0]["post_type"] == "story"
        assert PlatformPost.objects.get().platform_extra["post_type"] == "story"

    def test_reel_requires_one_video_and_story_requires_one_file(self, user, owner, workspace):
        account = _account(workspace, "facebook", "page-1")
        image = _asset(workspace, MediaAsset.MediaType.IMAGE, "pic.jpg")
        client = _client(user, workspace, [account])

        reel = client.post(
            "/api/v1/posts/",
            data=json.dumps(
                {
                    "social_account_id": str(account.id),
                    "caption": "nope",
                    "media_asset_ids": [str(image.id)],
                    "post_type": "reel",
                    "action": "draft",
                }
            ),
            content_type="application/json",
        )
        assert reel.status_code == 422
        assert "reel" in reel.json()["detail"]

        story = client.post(
            "/api/v1/posts/",
            data=json.dumps(
                {
                    "social_account_id": str(account.id),
                    "caption": "nope",
                    "post_type": "story",
                    "action": "draft",
                }
            ),
            content_type="application/json",
        )
        assert story.status_code == 422
        assert "story" in story.json()["detail"]
        assert not Post.objects.exists()

    def test_other_platforms_reject_post_type(self, user, owner, workspace):
        account = _account(workspace, "linkedin_personal", "li-1")
        client = _client(user, workspace, [account])

        response = client.post(
            "/api/v1/posts/",
            data=json.dumps(
                {
                    "social_account_id": str(account.id),
                    "caption": "hello",
                    "post_type": "feed",
                    "action": "draft",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 422
        assert "Instagram and Facebook" in response.json()["detail"]

    def test_omitted_post_type_keeps_todays_default(self, user, owner, workspace):
        account = _account(workspace, "instagram", "ig-1")
        video = _asset(workspace, MediaAsset.MediaType.VIDEO, "clip.mp4")
        client = _client(user, workspace, [account])

        response = client.post(
            "/api/v1/posts/",
            data=json.dumps(
                {
                    "social_account_id": str(account.id),
                    "caption": "default reel",
                    "media_asset_ids": [str(video.id)],
                    "action": "schedule",
                    "scheduled_at": _when(),
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 201, response.content
        assert response.json()["platform_posts"][0]["post_type"] == "feed"
        assert PlatformPost.objects.get().platform_extra == {}

    def test_update_and_schedule_accept_post_type(self, user, owner, workspace):
        account = _account(workspace, "instagram", "ig-1")
        image = _asset(workspace, MediaAsset.MediaType.IMAGE, "later.jpg")
        client = _client(user, workspace, [account])
        created = client.post(
            "/api/v1/posts/",
            data=json.dumps(
                {
                    "social_account_id": str(account.id),
                    "caption": "draft",
                    "media_asset_ids": [str(image.id)],
                    "action": "draft",
                }
            ),
            content_type="application/json",
        )
        assert created.status_code == 201, created.content
        post_id = created.json()["id"]

        updated = client.patch(
            f"/api/v1/posts/{post_id}",
            data=json.dumps({"post_type": "story"}),
            content_type="application/json",
        )
        assert updated.status_code == 200, updated.content
        assert updated.json()["platform_posts"][0]["post_type"] == "story"

        scheduled = client.post(
            f"/api/v1/posts/{post_id}/schedule",
            data=json.dumps({"scheduled_at": _when(), "post_type": "feed"}),
            content_type="application/json",
        )
        assert scheduled.status_code == 200, scheduled.content
        assert scheduled.json()["platform_posts"][0]["status"] == "scheduled"
        assert scheduled.json()["platform_posts"][0]["post_type"] == "feed"
        assert "post_type" not in PlatformPost.objects.get().platform_extra

    def test_invalid_post_type_is_a_validation_error(self, user, owner, workspace):
        account = _account(workspace, "instagram", "ig-1")
        client = _client(user, workspace, [account])
        response = client.post(
            "/api/v1/posts/",
            data=json.dumps(
                {
                    "social_account_id": str(account.id),
                    "caption": "nope",
                    "post_type": "carousel",
                    "action": "draft",
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == 422

    def test_mcp_schedule_post_stores_a_facebook_story(self, user, owner, workspace):
        account = _account(workspace, "facebook", "page-1")
        video = _asset(workspace, MediaAsset.MediaType.VIDEO, "mcp-story.mp4")
        client = _client(user, workspace, [account])
        rpc = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "schedule_post",
                "arguments": {
                    "social_account_id": str(account.id),
                    "caption": "video story",
                    "media_asset_ids": [str(video.id)],
                    "post_type": "story",
                    "scheduled_at": _when(),
                },
            },
        }
        response = client.post(MCP_URL, data=json.dumps(rpc), content_type="application/json")
        assert response.status_code == 200, response.content
        envelope = response.json()
        assert "error" not in envelope, envelope
        body = json.loads(envelope["result"]["content"][0]["text"])
        assert body["platform_posts"][0]["post_type"] == "story"
        assert body["platform_posts"][0]["status"] == "scheduled"
        assert PlatformPost.objects.get().platform_extra["post_type"] == "story"
