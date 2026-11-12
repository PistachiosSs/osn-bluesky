import os
import pandas as pd
from dotenv import load_dotenv
from atproto import Client

load_dotenv()
client = Client()
client.login(os.getenv("BSKY_HANDLE"), os.getenv("BSKY_APP_PASSWORD"))

QUERY = '"vibe coding" lang:en'      # exact phrase + English only

# Ask Bluesky for the 25 newest posts that match the query
response = client.app.bsky.feed.search_posts(
    params={"q": QUERY, "limit": 25, "sort": "latest"}
)

rows = []
for post in response.posts:          # go through each post
    record = post.record             # the post content itself
    rows.append({
        "uri": post.uri,                         # unique ID of the post
        "author": post.author.handle,            # who wrote it
        "created_at": record.created_at,         # when
        "text": record.text,                     # what it says
        "is_reply": record.reply is not None,    # is it a reply to someone?
        "replies": post.reply_count,             # how many replied to it
        "likes": post.like_count,
        "reposts": post.repost_count,
    })

df = pd.DataFrame(rows)              # turn the list into a table
print(f"Found {len(df)} posts for {QUERY}\n")

for _, r in df.head(5).iterrows():   # show the first 5
    print(r["created_at"][:10], "|", r["author"], "|", r["text"][:120].replace("\n", " "))
    print("-" * 60)

df.to_csv("step2_vibe_coding.csv", index=False, encoding="utf-8-sig")
print("Saved to step2_vibe_coding.csv")