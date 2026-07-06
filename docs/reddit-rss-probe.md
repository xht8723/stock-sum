# Reddit RSS Probe Findings

Observed on 2026-07-06 against `r/wallstreetbets` with direct HTTP requests. This is a research note only; no RSS collector was implemented.

## Listing Feed

Endpoint:

```text
https://www.reddit.com/r/wallstreetbets.rss
```

Observed behavior:

- Default fetch returned 25 Atom entries.
- `?limit=100` returned 100 Atom entries.
- `?limit=500` still returned 100 Atom entries, so the observed listing cap is 100 posts.
- The 100-entry live fetch's earliest post was published on 2026-06-30 13:43:49 local shell time:
  - title: `Palantir Acquires Pentagon For $800 Billion`
  - author: `/u/Chemical_Influence67`
  - link: `https://www.reddit.com/r/wallstreetbets/comments/1uk11dp/palantir_acquires_pentagon_for_800_billion/`

Fields available on listing entries:

- `title`
- `id`
- `updated`
- `published`
- `author`
- `category`
- `link`
- `content`

Fields not observed on listing entries:

- post score/upvotes
- comment count
- nested comment metadata

## Post Comment Feed

Endpoint shape:

```text
https://www.reddit.com/r/wallstreetbets/comments/{post_id}/{slug}.rss?limit=500
```

Observed behavior:

- A post comment RSS request returned the post as the first Atom entry.
- Remaining entries were comments.
- Example post `1uln7nr` returned 94 entries: one post entry plus about 93 comment entries.

Fields available on comment entries:

- `title`
- `id`, with comment IDs shaped like `t1_{comment_id}`
- `updated`
- `author`
- `link`
- `content`

Fields not observed on ordinary post comment feed entries:

- explicit `parent_id`
- explicit depth
- reply count
- `thr:in-reply-to`
- full ancestry path

## Nested Comment Feed

Endpoint shape:

```text
https://www.reddit.com/r/wallstreetbets/comments/{post_id}/comment/{comment_id}.rss
```

Confirmed nested-comment example:

```text
https://www.reddit.com/r/wallstreetbets/comments/1uosy1d/comment/ovvczrt.rss
```

Observed behavior:

- The feed returned the parent post first: `t3_1uosy1d`.
- It then returned the selected comment and nested comment IDs as `t1_*` entries.
- Repeated successful fetches returned 12, 13, and 15 entries as the thread grew.
- This endpoint can expose a selected comment thread/subtree, unlike the plain per-post comment feed.

Sample IDs from the nested feed:

```text
t3_1uosy1d
t1_ovvczrt
t1_ovvenoc
t1_ovvdp25
t1_ovvdhbp
t1_ovvdfba
t1_ovveddm
t1_ovvfis4
t1_ovvfuk6
t1_ovvddaf
t1_ovvd6r0
t1_ovvebop
t1_ovvegba
t1_ovvg0t2
t1_ovvd213
```

Important limitation:

- The nested endpoint exposes subtree entries, but the Atom entries still did not show explicit parent/depth fields in the inspected XML. If exact tree reconstruction is required, it may need inference from endpoint selection, ordering, or a second source.

## Rate Limiting

Reddit returned `429 Too Many Requests` during repeated RSS probing. No `Retry-After` header was observed.

Observed repeated requests to the nested comment RSS endpoint:

| Delay before request | Result |
| ---: | --- |
| 0s | `200`, 12 entries |
| 2s | `429` |
| 5s | `429` |
| 15s | `200`, 12 entries |
| 30s | `429` |
| 60s | `200`, 13 entries |
| 120s | `200`, 15 entries |

Interpretation:

- The block appears to operate on seconds/minutes scale, not days.
- The behavior is not a simple fixed minimum delay, because a 30-second request failed after a previous successful request.
- Treat it as a small rolling request bucket.

Practical throttle assumption:

- Use at least 60 seconds between comment-thread RSS requests.
- On `429`, back off to at least 120 seconds.
- Cache successful listing, post-comment, and nested-comment RSS responses aggressively.
- Avoid walking every comment thread in a subreddit listing unless the workflow can tolerate long runtimes.

## Fit As A Collector Alternative

RSS is potentially useful for a low-dependency Reddit fallback:

- It can fetch up to 100 recent subreddit posts.
- It can fetch per-post comment bodies.
- It can fetch selected nested comment subtrees through `/comment/{comment_id}.rss`.
- It does not provide upvotes/scores or comment totals in the observed Atom fields.
- It needs conservative throttling and caching to avoid `429`.
