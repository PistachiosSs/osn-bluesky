# Stop or Accelerate? The AI Debate in Software Development on Bluesky

Team 4 · COIN Online Social Networks (Uni Bamberg)
Ibad Ur Rehman · Huzaifa Jamal · Usama Bin Ayaz

## Research questions

**Big question:** How do people on Bluesky debate stopping or accelerating AI in software development?

- **RQ1 · Topics:** What are people discussing about AI in software development?
- **RQ2 · Attitudes:** What attitudes do they express: stop, accelerate, or in between?
- **RQ3 · Communities:** How do they interact and form communities?

## Pipeline

```
collector.py                                               step6_network.py            next
1 Search   ->  2 Screen   ->  3 Crawl    ->  4 Export   ->  5 Network           ->  6 Classify
keywords,      drop           follow every   posts.csv,     self-replies removed,   stance labels
date range,    off-topic      reply, all     edges.csv,     reply network,          from the codebook
English        conversations  levels deep    summary        Louvain communities
```

| Script | What it does |
|---|---|
| `collector.py` | Phases 1–4: searches Bluesky through the official API (logged in), screens conversations by their first post, crawls all replies, and exports the data. Saves progress in a SQLite database after every page and conversation, so it resumes after a crash. Retries rate limits and network errors. |
| `step6_network.py` | Phase 5: builds the user reply network (self-replies removed, weighted, undirected), finds communities with the Louvain method (seed 42), computes modularity and betweenness, and saves `network.png`, `network.gexf`, `users.csv` and `network_summary.txt`. |

### Keywords (pilot)

Searched in English, newest first. Quotes mean an exact phrase.

| Embrace / neutral side | Restrict side |
|---|---|
| `"vibe coding"` | `"anti-AI" code` |
| `"AI generated code"` | `"anti-AI" developers` |
| `"software engineers" AI` | `"no AI" code` |
| `"junior developers" AI` | `"AI slop" code` |
| `"replace developers"` | `"pause AI"` |
| `"AI coding" jobs` | `"AI regulation" developers` |
| `"learn to code" AI` | `"ban AI" code` |

### Settings (top of `collector.py`)

| Setting | Pilot value | Meaning |
|---|---|---|
| `SINCE` / `UNTIL` | `2026-10-01` / `2026-11-01` | Time window |
| `MAX_PAGES` | `3` | Pages per keyword (1 page = up to 100 posts, so max 300 posts per keyword) |
| `MAX_THREADS` | `None` | Crawl all on-topic conversations |
| `THREAD_DEPTH` | `100` | Reply levels to follow |
| `PAUSE` | `0.3` | Seconds between requests |
| `MAX_RETRIES` | `6` | Attempts for temporary errors |

**Screening rule:** a conversation is kept if its first post was a search hit, or if it mentions an AI term (AI, LLM, ChatGPT, Copilot, Claude, Cursor, Gemini, agentic, vibe coding) **and** a software term (code, coding, coder, dev, developer, programming, software, engineer).

## Setup

Requires Python 3.10 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

Create a file called `.env` in the project folder (it is ignored by Git):

```
BSKY_HANDLE=yourname.bsky.social
BSKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

Use a Bluesky **app password** (Settings → Privacy and Security → App Passwords), never your main password.

## Run

1. Set the keywords and the time window at the top of `collector.py`.
2. Collect the data. If it stops for any reason, run the same command again; it continues where it stopped.
   ```bash
   python collector.py                     # with the virtual environment activated
   .\.venv\Scripts\python.exe collector.py # Windows, without activating
   ```
3. Build the network. Set `DATA` at the top of `step6_network.py` to the output folder, then:
   ```bash
   python step6_network.py
   .\.venv\Scripts\python.exe step6_network.py
   ```

The collector also writes `bluesky_<start>_<end>.db` (its progress memory) and `collector_<start>_<end>.log` in the project folder.

Outputs go to `output_<start>_<end>/`: `posts.csv`, `edges.csv`, `conversations.csv`, `run_summary.json`, `users.csv`, `network.png`, `network.gexf` (open in Gephi).

## Pilot results (October 2026, 14 keywords)

| | |
|---|---|
| Posts collected | 8,117 (1,736 from search, 6,381 from conversations) |
| Conversations found | 1,077 |
| – crawled (on-topic) | 562 |
| – dropped as off-topic | 504 |
| – first post deleted or blocked | 11 |
| Reply links | 6,429 (1,612 self-replies, 4,817 between users) |
| Users in the reply network | 3,064 |
| Communities (10+ users) | 199 (38) |
| Modularity | 0.92 |
| Users in more than one conversation | 7% |

**Known limitations:**
- Each keyword is capped at 300 posts (`MAX_PAGES = 3`, newest first), so busy keywords only reach back a few days.
- Communities largely mirror single conversations, so the high modularity is partly an artefact.
- Replies inside conversations are not language-filtered.
- No bot detection yet.
- Stance is not yet labelled.

## Repository structure

```
collector.py        data collection (phases 1–4)
step6_network.py    reply network and communities (phase 5)
requirements.txt    Python packages (pinned versions)
learning/           earlier step-by-step scripts, replaced by collector.py; kept for reference
```

Not in the repository (ignored by Git): `.env`, `.venv/`, the `*.db` and `*.log` files, and the `output_*/` folders with the collected data.

## Data and ethics

- Only public posts, collected through Bluesky's official API.
- **Collected data is not part of this repository.** It is excluded by `.gitignore` and kept access-limited within the team.
- Users are anonymised in all reports and slides.
- Ethics approval is not required for this project under Uni Bamberg regulations, as confirmed by Prof. Janine Hacker (October 2026).
