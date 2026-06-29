"""Playwright X collector parser tests."""

from stock_sum.collectors.playwright.x import (
    X_SOURCE_TYPE,
    author_from_text,
    collect_visible_tweets,
    detect_blocked_timeline,
    extract_tweet_article,
    status_id_from_url,
    tweet_to_raw_item,
    x_post_sort_key,
)


class FakeLocator:
    def __init__(self, *, text="", attrs=None, locators=None, items=None):
        self.text = text
        self.attrs = attrs or {}
        self.locators = locators or {}
        self.items = items

    @property
    def first(self):
        if self.items:
            return self.items[0]
        return self

    def locator(self, selector):
        return self.locators.get(selector, FakeLocator(items=[]))

    def nth(self, index):
        return self.items[index]

    async def count(self):
        return len(self.items) if self.items is not None else 1

    async def get_attribute(self, name, timeout=None):
        return self.attrs.get(name)

    async def inner_text(self, timeout=None):
        return self.text


class FakePage:
    def __init__(self, *, title="", body="", articles=None):
        self._title = title
        self.body = FakeLocator(text=body)
        self.articles = FakeLocator(items=articles or [])

    async def title(self):
        return self._title

    def locator(self, selector):
        if selector == "body":
            return FakeLocator(items=[self.body])
        if selector == 'article[data-testid="tweet"], article':
            return self.articles
        return FakeLocator(items=[])


def _article(*, status_id, text, image_url=None, with_time=True):
    href = f"/aleabitoreddit/status/{status_id}"
    locators = {
        'a[href*="/status/"]': FakeLocator(items=[FakeLocator(attrs={"href": href})]),
    }
    if with_time:
        locators["time"] = FakeLocator(items=[FakeLocator(attrs={"datetime": "2026-06-27T12:00:00.000Z"})])
    if image_url:
        locators['img[src*="pbs.twimg.com/media"], img[src*="twimg.com/media"]'] = FakeLocator(
            items=[FakeLocator(attrs={"src": image_url, "alt": "chart"})]
        )
    return FakeLocator(text=text, locators=locators)


def test_status_id_from_url() -> None:
    assert status_id_from_url("https://x.com/user/status/1989352983348589023") == "1989352983348589023"
    assert status_id_from_url("/user/status/1989352983348589023/photo/1") == "1989352983348589023"
    assert status_id_from_url("https://x.com/user") is None


def test_author_from_text_uses_handle_line() -> None:
    text = "Serenity\n@aleabitoreddit\nPost text"

    assert author_from_text(text, fallback_handle="fallback") == ("aleabitoreddit", "Serenity")


async def test_extract_tweet_article_parses_text_timestamp_url_and_media() -> None:
    article = _article(
        status_id="1989352983348589023",
        text="Serenity\n@aleabitoreddit\nAI post text",
        image_url="https://pbs.twimg.com/media/G30jTFyXEAA-FZv.jpg",
    )

    tweet = await extract_tweet_article(article, handle="aleabitoreddit")

    assert tweet is not None
    assert tweet.status_id == "1989352983348589023"
    assert tweet.url == "https://x.com/aleabitoreddit/status/1989352983348589023"
    assert tweet.text == "AI post text"
    assert tweet.author_handle == "aleabitoreddit"
    assert tweet.author_name == "Serenity"
    assert tweet.media[0]["url"] == "https://pbs.twimg.com/media/G30jTFyXEAA-FZv.jpg"


async def test_extract_tweet_article_cleans_localized_date_header() -> None:
    article = _article(
        status_id="1989352983348589023",
        text="Serenity\n@aleabitoreddit\n2025年11月13日\n显示翻译\nClean post body\nShow more\nThis post is unavailable.",
        with_time=False,
    )

    tweet = await extract_tweet_article(article, handle="aleabitoreddit")

    assert tweet is not None
    assert tweet.posted_at_text == "2025年11月13日"
    assert tweet.text == "Clean post body"


async def test_collect_visible_tweets_deduplicates_by_status_id() -> None:
    tweets = {}
    page = FakePage(
        articles=[
            _article(status_id="2", text="@aleabitoreddit\nnew"),
            _article(status_id="1", text="@aleabitoreddit\nold"),
            _article(status_id="2", text="@aleabitoreddit\nnew duplicate"),
        ]
    )

    await collect_visible_tweets(page, handle="aleabitoreddit", tweets=tweets, optional_timeout_ms=1)

    assert set(tweets) == {"1", "2"}
    assert sorted(tweets.values(), key=lambda item: x_post_sort_key(item.status_id), reverse=True)[0].status_id == "2"


async def test_detect_blocked_timeline_reports_login_wall() -> None:
    page = FakePage(title="Log in to X", body="Sign in to X to continue")

    assert await detect_blocked_timeline(page) == "X timeline is not publicly available in headless mode: log in to x."


async def test_tweet_to_raw_item_uses_generic_x_source_type() -> None:
    tweet = await extract_tweet_article(
        _article(status_id="123", text="Serenity\n@aleabitoreddit\nhello"),
        handle="aleabitoreddit",
    )

    item = tweet_to_raw_item(tweet, handle="aleabitoreddit")

    assert item.source_type == X_SOURCE_TYPE
    assert item.source_id == "123"
    assert item.metadata["handle"] == "aleabitoreddit"
