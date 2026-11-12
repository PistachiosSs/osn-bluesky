"""
Bluesky collector v2 - robust and resumable.

What it does, in four phases:
  1. SEARCH  : every keyword, page by page, inside the time window
  2. SCREEN  : check the FIRST post of every conversation; keep only on-topic ones
  3. CRAWL   : walk the full reply tree of every on-topic conversation
  4. EXPORT  : write posts.csv, edges.csv and run_summary.json

Why it does not lose work:
  - Everything is saved to a small database file (SQLite) after every page and
    every conversation. If the script stops (crash, Wi-Fi, laptop closed), just run
    it again: it continues where it stopped instead of starting over.
  - Temporary errors (rate limits, network) are retried with increasing waits.
  - Permanent errors (deleted / blocked posts) are recorded and skipped.

Run:  python collector.py
"""
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from itertools import zip_longest

import pandas as pd
from atproto import Client
from dotenv import load_dotenv

# ======================= SETTINGS (change these) =======================
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
MAX_PAGES = 3          # pages per keyword (1 page = up to 100 posts)
MAX_THREADS = None     # None = crawl ALL on-topic conversations; or a number
THREAD_DEPTH = 100     # how many reply levels to follow
PAUSE = 0.3            # seconds between requests (be polite to the server)
MAX_RETRIES = 6        # attempts for temporary errors before giving up
# =======================================================================

# One database per time window, so different windows never mix
RUN_NAME = f"{SINCE[:10]}_{UNTIL[:10]}"
DB_FILE = f"bluesky_{RUN_NAME}.db"
OUT_DIR = f"output_{RUN_NAME}"

# A conversation is on-topic if its first post mentions AI AND software work
AI_TERMS = re.compile(r"\b(ai|llms?|chatgpt|copilot|claude|cursor|gemini|agentic|vibe.?cod\w*)\b", re.I)
SW_TERMS = re.compile(r"\b(code|coding|coders?|devs?|developers?|programm\w*|software|engineer\w*)\b", re.I)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(f"collector_{RUN_NAME}.log", encoding="utf-8"),
              logging.StreamHandler()],
)
logging.getLogger("httpx").setLevel(logging.WARNING)   # hide one line per request
log = logging.info


# ----------------------------------------------------------------------
# Database: the script's memory
# ----------------------------------------------------------------------
def open_db():
    db = sqlite3.connect(DB_FILE)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            uri TEXT PRIMARY KEY, author_did TEXT, author_handle TEXT, created_at TEXT,
            text TEXT, langs TEXT, parent_uri TEXT, root_uri TEXT,
            replies INTEGER, likes INTEGER, reposts INTEGER, quotes INTEGER,
            source TEXT, keywords TEXT);
        CREATE TABLE IF NOT EXISTS edges (
            reply_uri TEXT PRIMARY KEY, source_did TEXT, target_did TEXT,
            root_uri TEXT, created_at TEXT, is_self INTEGER);
        CREATE TABLE IF NOT EXISTS search_progress (
            keyword TEXT PRIMARY KEY, cursor TEXT, pages INTEGER, posts INTEGER, done INTEGER);
        CREATE TABLE IF NOT EXISTS conversations (
            root_uri TEXT PRIMARY KEY, keyword TEXT, on_topic INTEGER,
            status TEXT, reply_links INTEGER, error TEXT);
    """)
    return db


def save_post(db, post, source, keyword=""):
    """Insert a post, or add the keyword if we already have it."""
    row = db.execute("SELECT keywords FROM posts WHERE uri = ?", (post.uri,)).fetchone()
    if row:
        if keyword and keyword not in (row[0] or "").split(" | "):
            merged = " | ".join(k for k in [row[0], keyword] if k)
            db.execute("UPDATE posts SET keywords = ? WHERE uri = ?", (merged, post.uri))
        if source == "search":  # a search hit is stronger evidence than a thread find
            db.execute("UPDATE posts SET source = 'search' WHERE uri = ?", (post.uri,))
        return
    r = post.record
    reply = getattr(r, "reply", None)
    db.execute(
        "INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (post.uri, post.author.did, post.author.handle, getattr(r, "created_at", ""),
         getattr(r, "text", ""), ",".join(getattr(r, "langs", None) or []),
         reply.parent.uri if reply else "", reply.root.uri if reply else post.uri,
         post.reply_count or 0, post.like_count or 0, post.repost_count or 0,
         post.quote_count or 0, source, keyword),
    )


# ----------------------------------------------------------------------
# Safe API calls: retry temporary errors, skip permanent ones
# ----------------------------------------------------------------------
class PermanentError(Exception):
    """The request can never succeed (deleted, blocked, invalid) - skip it."""


def safe_call(client, func, params, what):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = func(params=params)
            time.sleep(PAUSE)
            return result
        except Exception as error:
            response = getattr(error, "response", None)
            status = getattr(response, "status_code", None)
            headers = getattr(response, "headers", None) or {}

            if status == 429:                                  # rate limited
                reset = headers.get("ratelimit-reset") or headers.get("RateLimit-Reset")
                wait = max(5, int(reset) - int(time.time())) if reset else 60
                wait = min(wait, 900)
                log(f"  rate limit reached - waiting {wait}s ({what})")
                time.sleep(wait)
                continue
            if status == 401:                                  # session expired
                log("  session expired - logging in again")
                login(client)
                continue
            if status is not None and 400 <= status < 500:     # deleted / blocked / invalid
                raise PermanentError(f"HTTP {status}: {str(error)[:150]}")

            wait = min(2 ** attempt, 120)                      # network or server error
            log(f"  {type(error).__name__} on {what} - retry {attempt}/{MAX_RETRIES} in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"gave up after {MAX_RETRIES} attempts: {what}")


def login(client):
    handle, password = os.getenv("BSKY_HANDLE"), os.getenv("BSKY_APP_PASSWORD")
    if not handle or not password:
        raise SystemExit("Missing BSKY_HANDLE or BSKY_APP_PASSWORD in your .env file.")
    client.login(handle, password)


# ----------------------------------------------------------------------
# Phase 1: search
# ----------------------------------------------------------------------
def phase_search(client, db):
    log("PHASE 1: searching keywords")
    for kw in KEYWORDS:
        row = db.execute("SELECT cursor, pages, posts, done FROM search_progress WHERE keyword = ?",
                         (kw,)).fetchone()
        cursor, pages, found, done = row if row else (None, 0, 0, 0)
        if done:
            log(f"  {kw:32} already done ({pages} pages, {found} posts)")
            continue
        while pages < MAX_PAGES:
            params = {"q": kw, "lang": "en", "since": SINCE, "until": UNTIL,
                      "limit": 100, "sort": "latest"}
            if cursor:
                params["cursor"] = cursor
            try:
                res = safe_call(client, client.app.bsky.feed.search_posts, params, f"search {kw}")
            except (PermanentError, RuntimeError) as error:
                log(f"  {kw}: stopped on page {pages + 1} ({error})")
                break
            pages += 1
            for post in res.posts:
                save_post(db, post, "search", kw)
            found += len(res.posts)
            cursor = res.cursor
            finished = not cursor or not res.posts
            db.execute("INSERT OR REPLACE INTO search_progress VALUES (?,?,?,?,?)",
                       (kw, cursor, pages, found, int(finished or pages >= MAX_PAGES)))
            db.commit()                                         # saved after every page
            if finished:
                break
        db.execute("UPDATE search_progress SET done = 1 WHERE keyword = ?", (kw,))
        db.commit()
        log(f"  {kw:32} pages={pages}  posts={found}")


# ----------------------------------------------------------------------
# Phase 2: screen conversations by their first post
# ----------------------------------------------------------------------
def is_on_topic(text):
    return bool(AI_TERMS.search(text or "") and SW_TERMS.search(text or ""))


def phase_screen(client, db):
    log("PHASE 2: screening conversations")
    rows = db.execute("""SELECT root_uri, keywords FROM posts
                         WHERE source = 'search' AND (replies > 0 OR parent_uri != '')""").fetchall()
    for root, keywords in rows:
        db.execute("INSERT OR IGNORE INTO conversations (root_uri, keyword, status) VALUES (?,?,'pending')",
                   (root, (keywords or "").split(" | ")[0]))
    db.commit()

    todo = [r[0] for r in db.execute("SELECT root_uri FROM conversations WHERE on_topic IS NULL")]
    texts = {}
    # roots we already have: a search hit always counts as on-topic (it matched our query)
    for uri in todo:
        row = db.execute("SELECT text, source FROM posts WHERE uri = ?", (uri,)).fetchone()
        if row:
            texts[uri] = (row[0], row[1] == "search")
    # roots we don't have: fetch them, 25 at a time
    missing = [u for u in todo if u not in texts]
    for i in range(0, len(missing), 25):
        batch = missing[i:i + 25]
        try:
            res = safe_call(client, client.app.bsky.feed.get_posts, {"uris": batch}, "get root posts")
            for post in res.posts:
                texts[post.uri] = (getattr(post.record, "text", ""), False)
        except (PermanentError, RuntimeError) as error:
            log(f"  could not fetch {len(batch)} root posts ({error})")

    for uri in todo:
        if uri not in texts:                                    # deleted or blocked root
            db.execute("UPDATE conversations SET on_topic = 0, status = 'missing' WHERE root_uri = ?", (uri,))
            continue
        text, from_search = texts[uri]
        keep = from_search or is_on_topic(text)
        db.execute("UPDATE conversations SET on_topic = ?, status = ? WHERE root_uri = ?",
                   (int(keep), "pending" if keep else "off_topic", uri))
    db.commit()

    counts = dict(db.execute("SELECT status, COUNT(*) FROM conversations GROUP BY status").fetchall())
    log(f"  conversations: {sum(counts.values())} total | "
        f"on-topic {counts.get('pending', 0) + counts.get('done', 0) + counts.get('failed', 0)} | "
        f"off-topic {counts.get('off_topic', 0)} | root missing {counts.get('missing', 0)}")


# ----------------------------------------------------------------------
# Phase 3: crawl full reply trees
# ----------------------------------------------------------------------
def crawl_one(client, db, root):
    res = safe_call(client, client.app.bsky.feed.get_post_thread,
                    {"uri": root, "depth": THREAD_DEPTH}, "thread")
    links = 0
    stack = [res.thread]                       # walk the tree without deep recursion
    while stack:
        node = stack.pop()
        if not hasattr(node, "post"):          # deleted or blocked reply
            continue
        save_post(db, node.post, "thread")
        for child in (getattr(node, "replies", None) or []):
            if not hasattr(child, "post"):
                continue
            src, tgt = child.post.author.did, node.post.author.did
            db.execute("INSERT OR IGNORE INTO edges VALUES (?,?,?,?,?,?)",
                       (child.post.uri, src, tgt, root,
                        getattr(child.post.record, "created_at", ""), int(src == tgt)))
            links += 1
            stack.append(child)
    return links


def phase_crawl(client, db):
    log("PHASE 3: crawling conversations")
    pending = db.execute("SELECT root_uri, keyword FROM conversations WHERE status = 'pending'").fetchall()
    # take conversations from every keyword in turn, so no keyword dominates
    by_kw = {}
    for root, kw in pending:
        by_kw.setdefault(kw, []).append(root)
    order = [r for group in zip_longest(*by_kw.values()) for r in group if r]

    already = db.execute("SELECT COUNT(*) FROM conversations WHERE status = 'done'").fetchone()[0]
    if MAX_THREADS is not None:
        order = order[:max(0, MAX_THREADS - already)]
    log(f"  {already} already crawled, {len(order)} to go")

    for i, root in enumerate(order, 1):
        try:
            links = crawl_one(client, db, root)
            db.execute("UPDATE conversations SET status = 'done', reply_links = ? WHERE root_uri = ?",
                       (links, root))
        except (PermanentError, RuntimeError) as error:
            db.execute("UPDATE conversations SET status = 'failed', error = ? WHERE root_uri = ?",
                       (str(error)[:200], root))
        db.commit()                                             # saved after every conversation
        if i % 25 == 0 or i == len(order):
            total = db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            log(f"  {i}/{len(order)} conversations, {total} reply links in total")


# ----------------------------------------------------------------------
# Phase 4: export
# ----------------------------------------------------------------------
def phase_export(db):
    log("PHASE 4: exporting")
    os.makedirs(OUT_DIR, exist_ok=True)
    posts = pd.read_sql("SELECT * FROM posts", db)
    edges = pd.read_sql("SELECT * FROM edges", db)
    convs = pd.read_sql("SELECT * FROM conversations", db)
    posts.to_csv(f"{OUT_DIR}/posts.csv", index=False, encoding="utf-8-sig")
    edges.to_csv(f"{OUT_DIR}/edges.csv", index=False, encoding="utf-8-sig")
    convs.to_csv(f"{OUT_DIR}/conversations.csv", index=False, encoding="utf-8-sig")

    real = edges[edges["is_self"] == 0]
    sizes = convs.loc[convs["status"] == "done", "reply_links"]
    summary = {
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "time_window": [SINCE, UNTIL],
        "keywords": KEYWORDS,
        "settings": {"max_pages": MAX_PAGES, "max_threads": MAX_THREADS, "thread_depth": THREAD_DEPTH},
        "posts_total": len(posts),
        "posts_from_search": int((posts["source"] == "search").sum()),
        "posts_from_conversations": int((posts["source"] == "thread").sum()),
        "conversations": convs["status"].value_counts().to_dict(),
        "reply_links_total": len(edges),
        "self_replies": int(edges["is_self"].sum()) if len(edges) else 0,
        "reply_links_between_users": len(real),
        "users_in_network": int(pd.concat([real["source_did"], real["target_did"]]).nunique()) if len(real) else 0,
        "largest_conversation_share": round(sizes.max() / sizes.sum(), 3) if len(sizes) and sizes.sum() else 0,
    }
    with open(f"{OUT_DIR}/run_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    log("SUMMARY\n" + json.dumps(summary, indent=2))
    log(f"Saved to folder {OUT_DIR}/")


def main():
    load_dotenv()
    client = Client()
    login(client)
    db = open_db()
    try:
        phase_search(client, db)
        phase_screen(client, db)
        phase_crawl(client, db)
    except KeyboardInterrupt:
        log("Stopped by you. Progress is saved - run again to continue.")
    finally:
        db.commit()
        phase_export(db)
        db.close()


if __name__ == "__main__":
    main()