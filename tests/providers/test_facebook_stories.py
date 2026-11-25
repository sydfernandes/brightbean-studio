"""Facebook Page Stories: unpublished photo, then photo_stories; video start/upload/finish.

Feed photos, Page videos, and Reels keep the endpoints they already used.
"""

from unittest.mock import MagicMock, call

import pytest

from providers.exceptions import PublishError
from providers.facebook import FacebookProvider
from providers.types import PostType, PublishContent

PAGE = "https://graph.facebook.com/v25.0/page-1"
IMAGE = "https://cdn.example/pic.jpg?X-Amz-Signature=def"
VIDEO = "https://cdn.example/clip.mp4?X-Amz-Signature=abc"


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _provider():
    provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
    return provider


def test_photo_story_uploads_unpublished_then_calls_photo_stories():
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "photo-9"}),
            _resp({"success": True, "post_id": "page-1_story-1"}),
        ]
    )

    result = provider.publish_post(
        "page-token",
        PublishContent(
            text="not shown on a story",
            media_urls=[IMAGE],
            media_types=["image"],
            post_type=PostType.STORY,
            extra={"page_id": "page-1"},
        ),
    )

    assert result.platform_post_id == "story-1"
    assert result.extra == {"photo_id": "photo-9", "post_id": "page-1_story-1"}
    assert "post_type" not in result.extra
    assert provider._request.call_args_list == [
        call(
            "POST",
            f"{PAGE}/photos",
            access_token="page-token",
            json={"url": IMAGE, "published": False},
        ),
        call(
            "POST",
            f"{PAGE}/photo_stories",
            access_token="page-token",
            json={"photo_id": "photo-9"},
        ),
    ]


def test_photo_story_deletes_the_staged_photo_when_publish_fails():
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "photo-9"}),
            _resp({"success": False, "error": {"message": "nope"}}),
            _resp({"success": True}),
        ]
    )

    with pytest.raises(PublishError):
        provider.publish_post(
            "page-token",
            PublishContent(
                media_urls=[IMAGE],
                media_types=["image"],
                post_type=PostType.STORY,
                extra={"page_id": "page-1"},
            ),
        )

    assert provider._request.call_args_list[-1] == call(
        "DELETE",
        "https://graph.facebook.com/v25.0/photo-9",
        access_token="page-token",
    )


def test_video_story_uses_start_upload_finish_on_video_stories():
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            _resp({"video_id": "story-video-1", "upload_url": "https://rupload.facebook.com/story-upload"}),
            _resp({"success": True}),
            _resp({"success": True, "post_id": "page-1_story-2"}),
            _resp({"post_id": "page-1_story-2", "permalink_url": "https://www.facebook.com/stories/story-video-1"}),
        ]
    )

    result = provider.publish_post(
        "page-token",
        PublishContent(
            text="caption stays off the story",
            media_urls=[VIDEO],
            media_types=["video"],
            post_type=PostType.STORY,
            video_duration_sec=15,
            extra={"page_id": "page-1"},
        ),
    )

    assert result.platform_post_id == "story-2"
    assert result.extra["video_id"] == "story-video-1"
    assert "post_type" not in result.extra
    assert provider._request.call_args_list == [
        call(
            "POST",
            f"{PAGE}/video_stories",
            access_token="page-token",
            data={"upload_phase": "start"},
        ),
        call(
            "POST",
            "https://rupload.facebook.com/story-upload",
            headers={"Authorization": "OAuth page-token", "file_url": VIDEO},
        ),
        call(
            "POST",
            f"{PAGE}/video_stories",
            access_token="page-token",
            data={"upload_phase": "finish", "video_id": "story-video-1"},
        ),
        call(
            "GET",
            "https://graph.facebook.com/v25.0/story-video-1",
            access_token="page-token",
            params={"fields": "post_id,permalink_url"},
        ),
    ]


def test_a_long_video_story_is_refused_before_any_request():
    provider = _provider()
    provider._request = MagicMock()

    with pytest.raises(PublishError, match="60 seconds"):
        provider.publish_post(
            "page-token",
            PublishContent(
                media_urls=[VIDEO],
                media_types=["video"],
                post_type=PostType.STORY,
                video_duration_sec=90,
                extra={"page_id": "page-1"},
            ),
        )

    provider._request.assert_not_called()


def test_feed_photo_page_video_and_reel_paths_are_unchanged():
    provider = _provider()
    provider._request = MagicMock(side_effect=[_resp({"id": "photo-1", "post_id": "page-1_post-1"})])
    provider.publish_post(
        "page-token",
        PublishContent(text="feed", media_urls=[IMAGE], post_type=PostType.IMAGE, extra={"page_id": "page-1"}),
    )
    assert provider._request.call_args_list[0].args[1] == f"{PAGE}/photos"
    assert "published" not in provider._request.call_args_list[0].kwargs["json"]

    provider._request = MagicMock(side_effect=[_resp({"id": "video-1"}), _resp({})])
    provider.publish_post(
        "page-token",
        PublishContent(text="feed video", media_urls=[VIDEO], post_type=PostType.VIDEO, extra={"page_id": "page-1"}),
    )
    assert provider._request.call_args_list[0].args[1] == f"{PAGE}/videos"
    assert provider._request.call_args_list[0].kwargs["json"]["description"] == "feed video"

    provider._request = MagicMock(
        side_effect=[
            _resp({"video_id": "reel-1", "upload_url": "https://rupload.facebook.com/reel"}),
            _resp({"success": True}),
            _resp({"success": True}),
            _resp({}),
        ]
    )
    provider.publish_post(
        "page-token",
        PublishContent(
            text="reel",
            media_urls=[VIDEO],
            post_type=PostType.REEL,
            video_duration_sec=30,
            extra={"page_id": "page-1"},
        ),
    )
    assert provider._request.call_args_list[0].args[1] == f"{PAGE}/video_reels"
    assert provider._request.call_args_list[2].kwargs["data"]["description"] == "reel"
