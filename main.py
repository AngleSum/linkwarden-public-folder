import os
import json
import time
from pathlib import Path
import requests

# Initial Setup
STATE_FILE = Path("./data/user_state.json")
BASE_URL = os.getenv("LINKWARDEN_URL", "http://linkwarden:3000")
API_TOKEN = os.getenv("LINKWARDEN_TOKEN")
ROOT_COLLECTION_ID = os.getenv("ROOT_COLLECTION_ID") # From the URL of the collection

# Validate environment variables
if not API_TOKEN or not API_TOKEN.strip():
    raise RuntimeError("Missing LINKWARDEN_TOKEN env var")
if not ROOT_COLLECTION_ID or not ROOT_COLLECTION_ID.strip():
    raise RuntimeError("Missing ROOT_COLLECTION_ID env var")
try:
    ROOT_COLLECTION_ID = int(ROOT_COLLECTION_ID)
except ValueError:
    raise RuntimeError("ROOT_COLLECTION_ID must be an integer")

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN.strip()}",
    "Content-Type": "application/json",
}

# Helpers
def load_or_init_state():
    # Ensure the data directory exists
    STATE_FILE.parent.mkdir(exist_ok=True)
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"known_user_ids": []}

def atomic_write_state(state):
    tmp_file = STATE_FILE.with_suffix(".tmp")
    try:
        with open(tmp_file, "w") as f:
            json.dump(state, f, indent=2)
        tmp_file.replace(STATE_FILE)
    except OSError as e:
        print(f"Failed to write state to {tmp_file}: {e}")
        raise

# API functions
def fetch_all_user_ids():
    url = f"{BASE_URL}/api/v1/users"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        print(f"Request URL: {url}, Status: {resp.status_code}, Body: {resp.text[:200]}")
        resp.raise_for_status()
        data = resp.json()
        return [u["id"] for u in data["response"]]
    except requests.exceptions.RequestException as e:
        print(f"Error fetching users: {e}")
        raise

def fetch_all_collections():
    url = f"{BASE_URL}/api/v1/collections"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data["response"]
    except requests.exceptions.RequestException as e:
        print(f"Error fetching collections: {e}")
        raise

def get_collection(collection_id):
    url = f"{BASE_URL}/api/v1/collections/{collection_id}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data["response"]
    except requests.exceptions.RequestException as e:
        print(f"Error fetching collection {collection_id}: {e}")
        raise

def compute_descendants_of(collections, root_id):
    children_map = {}
    for c in collections:
        pid = c.get("parentId")
        if pid:
            children_map.setdefault(pid, []).append(c["id"])

    descendants = []
    def walk(cid):
        for child in children_map.get(cid, []):
            descendants.append(child)
            walk(child)
    walk(root_id)
    return descendants

def ensure_permissions(user_id, collection_id, full_access):
    coll = get_collection(collection_id)

    clean_members = [
        {
            "userId": m["userId"],
            "canCreate": m["canCreate"],
            "canUpdate": m["canUpdate"],
            "canDelete": m["canDelete"],
        }
        for m in coll.get("members", [])
    ]

    found = False
    for m in clean_members:
        if m["userId"] == user_id:
            found = True
            m["canCreate"] = full_access
            m["canUpdate"] = full_access
            m["canDelete"] = full_access
    if not found:
        clean_members.append({
            "userId": user_id,
            "canCreate": full_access,
            "canUpdate": full_access,
            "canDelete": full_access,
        })

    url = f"{BASE_URL}/api/v1/collections/{collection_id}"
    payload = {
        "id": coll["id"],
        "name": coll["name"],
        "description": coll.get("description", ""),
        "color": coll.get("color"),
        "icon": coll.get("icon"),
        "iconWeight": coll.get("iconWeight"),
        "parentId": coll.get("parentId"),
        "isPublic": coll.get("isPublic", False),
        "members": clean_members,
    }

    try:
        put = requests.put(url, headers=HEADERS, json=payload, timeout=10)
        if not put.ok:
            print(f"Error updating collection {collection_id}: {put.status_code}, {put.text}")
        put.raise_for_status()
        print(f"Updated permissions for user {user_id} on collection {collection_id}")
    except requests.exceptions.RequestException as e:
        print(f"Error updating collection {collection_id}: {e}")
        raise

def main():
    while True:
        state = load_or_init_state()
        known_ids = set(state["known_user_ids"])

        user_ids = set(fetch_all_user_ids())
        new_users = user_ids - known_ids

        if new_users:
            print(f"New users detected: {new_users}")
            collections = fetch_all_collections()
            descendants = compute_descendants_of(collections, ROOT_COLLECTION_ID)

            for uid in new_users:
                ensure_permissions(uid, ROOT_COLLECTION_ID, full_access=True)
                for c in descendants:
                    ensure_permissions(uid, c, full_access=False)

            state["known_user_ids"] = sorted(user_ids)
            atomic_write_state(state)
        else:
            print("No new users")

        time.sleep(int(os.getenv("POLL_INTERVAL", "60")))  # interval: 60s

if __name__ == "__main__":
    main()