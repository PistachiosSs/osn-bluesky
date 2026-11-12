"""
Step 6 - build the reply network, find communities and bridge users.

Input : output_.../edges.csv and posts.csv (from collector.py)
Output: network_summary.txt, network.png, network.gexf (open in Gephi), users.csv
Run   : python step6_network.py
"""
import matplotlib
matplotlib.use("Agg")                     # save pictures without opening a window
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

DATA = "output_2026-10-01_2026-11-01"     # folder made by collector.py
SEED = 42                                 # fixed seed = same result every run

edges = pd.read_csv(f"{DATA}/edges.csv")
posts = pd.read_csv(f"{DATA}/posts.csv")
edges = edges[edges["is_self"] == 0]      # people replying to themselves are not interactions

# ---------- 1. Build the graph ----------
# One node per user. One edge per pair of users who replied to each other.
# weight = how many times they replied to each other (in either direction).
pairs = edges.apply(lambda r: tuple(sorted([r["source_did"], r["target_did"]])), axis=1)
weights = pairs.value_counts()
G = nx.Graph()
for (a, b), w in weights.items():
    G.add_edge(a, b, weight=int(w))

# How many different conversations each user took part in
convs_per_user = pd.concat([
    edges[["source_did", "root_uri"]].rename(columns={"source_did": "did"}),
    edges[["target_did", "root_uri"]].rename(columns={"target_did": "did"}),
]).drop_duplicates().groupby("did")["root_uri"].nunique()

components = sorted(nx.connected_components(G), key=len, reverse=True)
giant = G.subgraph(components[0]).copy()

# ---------- 2. Communities (Louvain method) ----------
communities = nx.community.louvain_communities(G, weight="weight", seed=SEED)
communities = sorted(communities, key=len, reverse=True)
community_of = {user: i for i, c in enumerate(communities) for user in c}
modularity = nx.community.modularity(G, communities, weight="weight")

# ---------- 3. Important users ----------
degree = dict(G.degree())                                    # how many people they talk to
k = min(500, G.number_of_nodes())                            # sample makes it fast on big graphs
betweenness = nx.betweenness_centrality(G, k=k, seed=SEED)   # how often they sit "between" others


def bridge_score(user):
    """How many OTHER communities this user is directly connected to."""
    own = community_of[user]
    return len({community_of[n] for n in G.neighbors(user)} - {own})


handle_of = posts.drop_duplicates("author_did").set_index("author_did")["author_handle"].to_dict()
users = pd.DataFrame({
    "did": list(G.nodes()),
    "handle": [handle_of.get(u, "") for u in G.nodes()],
    "community": [community_of[u] for u in G.nodes()],
    "degree": [degree[u] for u in G.nodes()],
    "betweenness": [round(betweenness[u], 5) for u in G.nodes()],
    "other_communities_reached": [bridge_score(u) for u in G.nodes()],
    "conversations": [int(convs_per_user.get(u, 0)) for u in G.nodes()],
}).sort_values("betweenness", ascending=False)
users.to_csv(f"{DATA}/users.csv", index=False, encoding="utf-8-sig")

# ---------- 4. Summary ----------
sizes = [len(c) for c in communities]
lines = [
    "REPLY NETWORK SUMMARY",
    f"Users (nodes):                     {G.number_of_nodes()}",
    f"User pairs who interacted (edges): {G.number_of_edges()}",
    f"Separate groups (components):      {len(components)}",
    f"Largest connected group:           {giant.number_of_nodes()} users "
    f"({giant.number_of_nodes() / G.number_of_nodes():.0%})",
    f"Users in 2+ conversations:         {(convs_per_user >= 2).sum()} "
    f"({(convs_per_user >= 2).mean():.0%})",
    "",
    f"Communities found:                 {len(communities)}",
    f"Communities with 10+ users:        {sum(s >= 10 for s in sizes)}",
    f"Largest communities (users):       {sizes[:10]}",
    f"Modularity:                        {modularity:.2f}   (above ~0.3 = clear community structure)",
    "",
    "Top 10 bridge users (high betweenness) - for your eyes only, NOT for slides:",
    users.head(10)[["handle", "community", "degree", "betweenness",
                    "other_communities_reached", "conversations"]].to_string(index=False),
]
summary = "\n".join(lines)
print(summary)
with open(f"{DATA}/network_summary.txt", "w", encoding="utf-8") as f:
    f.write(summary)

# ---------- 5. Picture of the largest connected group ----------
palette = plt.get_cmap("tab20").colors
top_communities = {c for c in range(min(12, len(communities)))}
colors = [palette[community_of[u] % 20] if community_of[u] in top_communities else (0.8, 0.8, 0.8)
          for u in giant.nodes()]
node_sizes = [8 + 4 * giant.degree(u) for u in giant.nodes()]
pos = nx.spring_layout(giant, seed=SEED, k=0.15, iterations=60)

fig, ax = plt.subplots(figsize=(12, 12))
nx.draw_networkx_edges(giant, pos, ax=ax, alpha=0.15, width=0.5)
nx.draw_networkx_nodes(giant, pos, ax=ax, node_color=colors, node_size=node_sizes, linewidths=0)
ax.set_title(f"Bluesky reply network - largest connected group\n"
             f"{giant.number_of_nodes()} users, colour = community (Louvain), size = number of contacts",
             fontsize=13)
ax.axis("off")
fig.savefig(f"{DATA}/network.png", dpi=200, bbox_inches="tight")

# Full network for Gephi (nicer pictures, interactive exploring)
nx.set_node_attributes(G, community_of, "community")
nx.write_gexf(G, f"{DATA}/network.gexf")
print(f"\nSaved network.png, network.gexf, users.csv and network_summary.txt in {DATA}/")