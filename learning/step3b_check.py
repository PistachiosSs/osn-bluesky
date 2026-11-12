import pandas as pd
p = pd.read_csv("posts.csv")
e = pd.read_csv("edges.csv")

print("English share:", round(p["langs"].fillna("").str.contains("en").mean() * 100), "%")
print("\nBiggest conversations (number of reply links):")
print(e["root_uri"].value_counts().head(5))
print("\nSelf-replies (people threading their own posts):", (e["source_did"] == e["target_did"]).sum())