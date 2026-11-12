import os
import time
import pandas as pd
from dotenv import load_dotenv
from atproto import Client

# ================= SETTINGS (change these) =================
KEYWORDS = [
    # embrace / neutral side
    '"vibe coding"',
    '"AI generated code"',
    '"software engineers" AI',
    '"junior developers" AI',
    '"replace developers"',
    '"AI coding" jobs',
    '"learn to code" AI',
    # restrict side
    '"anti-AI" code',
    '"anti-AI" developers',
    '"no AI" code',
    '"AI slop" code',
    '"pause AI"',
    '"AI regulation" developers',
    '"ban AI" code',
]
SINCE = "2026-10-01T00:00:00Z"   # start of time window
UNTIL = "2026-11-01T00:00:00Z"   # end of time window
MAX_PAGES = 3        # pages per keyword (1 page = up to 100 posts)
MAX_THREADS = 100    # how many conversations to crawl completely
PAUSE = 0.3          # seconds to wait between requests (be polite)
# ===========================================================

load_dotenv()
client = Client()
client.login(os.getenv("BSKY_HANDLE"), os.getenv("BSKY_APP_PASSWORD"))

posts = {}   # uri -> post row (a dictionary automatically prevents duplicates)
edges = []   # one row per reply: who replied to whom


def save_post(post, source, keyword=""):
    """Store one post. If we already have it, just add the keyword."""
    if post.uri in posts:
        row = posts[post.uri]
        if keyword and keyword not in row["keywords"]:
            row["keywords"] = (row["keywords"] + " | " + keyword).strip(" |")
        return
    r = post.record
    posts[post.uri] = {
        "uri": post.uri,
        "author_did": post.author.did,          # permanent user ID
        "author_handle": post.author.handle,    # readable name (can change)
        "created_at": r.created_at,
        "text": r.text,
        "langs": ",".join(r.langs or []),
        "parent_uri": r.reply.parent.uri if r.reply else "",   # post it answers
        "root_uri": r.reply.root.uri if r.reply else post.uri, # start of conversation
        "replies": post.reply_count,
        "likes": post.like_count,
        "reposts": post.repost_count,
        "source": source,                       # "search" or "thread"
        "keywords": keyword,                    # which search found it
    }


# ---------- PART 1: search every keyword, page by page ----------
print("PART 1: searching keywords")
for kw in KEYWORDS:
    cursor, page, found = None, 0, 0
    while page < MAX_PAGES:
        params = {"q": kw, "lang": "en", "since": SINCE, "until": UNTIL,
                  "limit": 100, "sort": "latest"}
        if cursor:
            params["cursor"] = cursor          # "give me the NEXT page"
        res = client.app.bsky.feed.search_posts(params=params)
        page += 1
        for p in res.posts:
            save_post(p, "search", kw)
        found += len(res.posts)
        cursor = res.cursor                    # no cursor = no more pages
        if not cursor or not res.posts:
            break
        time.sleep(PAUSE)
    print(f"  {kw:35} pages={page}  posts={found}")


# ---------- PART 2: open each conversation and walk all replies ----------
def walk(node, root_uri):
    """Visit a post, then every reply under it, then replies to those, ..."""
    if not hasattr(node, "post"):              # deleted or blocked post
        return
    save_post(node.post, "thread")
    for child in (node.replies or []):
        if hasattr(child, "post"):
            edges.append({
                "source_did": child.post.author.did,    # the person replying
                "target_did": node.post.author.did,     # the person replied to
                "reply_uri": child.post.uri,
                "root_uri": root_uri,
                "created_at": child.post.record.created_at,
            })
            walk(child, root_uri)              # go one level deeper


from itertools import zip_longest

search_rows = [r for r in posts.values() if r["source"] == "search"]

# group conversations by the keyword that found them
by_kw = {}
for r in search_rows:
    if r["replies"] or r["parent_uri"]:
        kw = r["keywords"].split(" | ")[0]
        by_kw.setdefault(kw, []).append(r["root_uri"])

# take one from each keyword in turn: kw1, kw2, kw3, ..., then kw1 again
roots = []
for group in zip_longest(*by_kw.values()):
    for root in group:
        if root and root not in roots:
            roots.append(root)

print(f"\nPART 2: crawling {min(len(roots), MAX_THREADS)} of {len(roots)} conversations")
for i, root in enumerate(roots[:MAX_THREADS], 1):
    try:
        res = client.app.bsky.feed.get_post_thread(params={"uri": root, "depth": 50})
        walk(res.thread, root)
    except Exception as e:
        print(f"  skipped one conversation ({type(e).__name__})")
    if i % 10 == 0:
        print(f"  {i} conversations done, {len(edges)} reply links so far")
    time.sleep(PAUSE)


# ---------- PART 3: save ----------
posts_df = pd.DataFrame(posts.values())
posts_df.to_csv("posts.csv", index=False, encoding="utf-8-sig")
edges_df = pd.DataFrame(edges)
if not edges_df.empty:
    edges_df = edges_df.drop_duplicates("reply_uri")
edges_df.to_csv("edges.csv", index=False, encoding="utf-8-sig")

print("\nSUMMARY")
print("  posts total:         ", len(posts_df))
print("  from search:         ", (posts_df["source"] == "search").sum())
print("  from conversations:  ", (posts_df["source"] == "thread").sum())
print("  reply links (edges): ", len(edges_df))
print("  unique users:        ", posts_df["author_did"].nunique())
print("Saved posts.csv and edges.csv")