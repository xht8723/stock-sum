"""X collector parsing and filtering tests."""

from stock_sum.collectors.playwright.x import (
    XPostData,
    _launch_options,
    extract_status_id,
    is_login_or_blocked_text,
    normalize_handle,
    post_data_to_raw_item,
    should_skip_post,
)


def test_normalize_handle() -> None:
    assert normalize_handle("@aleabitoreddit") == "aleabitoreddit"
    assert normalize_handle(" aleabitoreddit ") == "aleabitoreddit"


def test_extract_status_id() -> None:
    assert extract_status_id("https://x.com/user/status/12345") == "12345"
    assert extract_status_id("https://x.com/i/web/status/67890") == "67890"
    assert extract_status_id("https://x.com/user") is None


def test_login_or_blocked_text_detection() -> None:
    assert is_login_or_blocked_text("Log in to X to see more")
    assert is_login_or_blocked_text("Something went wrong. Try reloading.")
    assert not is_login_or_blocked_text("A normal timeline post")


def test_post_filtering() -> None:
    assert should_skip_post("Pinned\nUseful market post")
    assert should_skip_post("Promoted\nAd copy")
    assert not should_skip_post("Useful market post")
    assert not should_skip_post("Pinned\nUseful market post", include_pinned=True)


def test_post_data_to_raw_item() -> None:
    item = post_data_to_raw_item(
        XPostData(
            status_id="12345",
            url="https://x.com/user/status/12345",
            text="market note",
            author="User @user",
            timestamp="2026-06-26T12:00:00.000Z",
            handle="user",
        )
    )

    assert item.source_id == "12345"
    assert item.source_type == "x_user_timeline"
    assert item.metadata["platform"] == "x"
    assert item.metadata["handle"] == "user"


def test_launch_options_include_optional_channel(tmp_path) -> None:
    without_channel = _launch_options(user_data_dir=tmp_path, headless=True)
    with_channel = _launch_options(user_data_dir=tmp_path, headless=False, channel="chrome")

    assert "channel" not in without_channel
    assert with_channel["channel"] == "chrome"
