import os                      # lets Python read settings from your system
from dotenv import load_dotenv # reads the .env file
from atproto import Client     # the Bluesky client

load_dotenv()                                  # load the .env values
handle = os.getenv("BSKY_HANDLE")              # your username
password = os.getenv("BSKY_APP_PASSWORD")      # your app password

client = Client()                              # create a connection object
profile = client.login(handle, password)       # log in, returns your profile

print("Logged in as:", profile.display_name, f"(@{profile.handle})")
print("Followers:", profile.followers_count)
print("Posts:", profile.posts_count)