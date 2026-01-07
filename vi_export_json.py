import os, time, json, subprocess
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from azure.storage.blob import (
    BlobServiceClient,
    generate_blob_sas,
    BlobSasPermissions,
)

API_VERSION = "2025-04-01"  # VI ARM generateAccessToken API version :contentReference[oaicite:5]{index=5}

def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v

import shutil
import subprocess

def az_cli_access_token() -> str:
    az = shutil.which("az.cmd") or shutil.which("az") or shutil.which("az.exe")
    if not az:
        raise RuntimeError(
            "Azure CLI not found. Make sure az.cmd is on PATH. "
            "Try restarting PowerShell."
        )

    cmd = [
        az, "account", "get-access-token",
        "--resource", "https://management.azure.com/",
        "--query", "accessToken",
        "-o", "tsv",
    ]
    return subprocess.check_output(cmd, text=True).strip()


def vi_access_token(subscription_id: str, rg: str, vi_account_name: str) -> str:
    # ARM generateAccessToken :contentReference[oaicite:6]{index=6}
    mgmt_token = az_cli_access_token()
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.VideoIndexer/accounts/{vi_account_name}"
        f"/generateAccessToken?api-version={API_VERSION}"
    )
    body = {"permissionType": "Contributor", "scope": "Account"}
    r = requests.post(url, headers={"Authorization": f"Bearer {mgmt_token}"}, json=body, timeout=60)
    r.raise_for_status()
    return r.json()["accessToken"]

def upload_to_blob_and_get_sas(conn_str: str, container: str, video_path: str) -> str:
    bsc = BlobServiceClient.from_connection_string(conn_str)
    blob_name = f"uploads/{int(time.time())}_{os.path.basename(video_path)}"
    blob_client = bsc.get_blob_client(container=container, blob=blob_name)

    with open(video_path, "rb") as f:
        blob_client.upload_blob(f, overwrite=True)

    # MVP SAS using account key from connection string
    account_name = bsc.account_name
    account_key = [p.split("=", 1)[1] for p in conn_str.split(";") if p.startswith("AccountKey=")][0]

    sas = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=6),
    )
    return f"https://{account_name}.blob.core.windows.net/{container}/{blob_name}?{sas}"

def vi_upload_and_index(location: str, account_id: str, api_key: str, bearer: str, video_url: str, video_name: str) -> str:
    # Upload by URL (recommended pattern; SAS URL is valid) :contentReference[oaicite:7]{index=7}
    base = f"https://api.videoindexer.ai/{location}/Accounts/{account_id}/Videos"
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Ocp-Apim-Subscription-Key": api_key,
    }
    params = {
        "name": video_name,
        "privacy": "Private",
        "priority": "Low",
        "language": "auto",
        "indexingPreset": "Default",
        "streamingPreset": "Default",
        "sendSuccessEmail": "false",
        "videoUrl": video_url,
    }
    r = requests.post(base, headers=headers, params=params, timeout=120)
    r.raise_for_status()
    return r.json()["id"]

def vi_get_index(location: str, account_id: str, api_key: str, bearer: str, video_id: str) -> dict:
    url = f"https://api.videoindexer.ai/{location}/Accounts/{account_id}/Videos/{video_id}/Index"
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Ocp-Apim-Subscription-Key": api_key,
    }
    r = requests.get(url, headers=headers, timeout=120)
    if r.status_code >= 400:
        print("Status:", r.status_code)

    r.raise_for_status()
    return r.json()

def main():
    load_dotenv()

    subscription_id = require_env("AZ_SUBSCRIPTION_ID")
    rg = require_env("AZ_RESOURCE_GROUP")
    vi_account_name = require_env("VI_ACCOUNT_NAME")
    location = require_env("VI_LOCATION")
    account_id = require_env("VI_ACCOUNT_ID")
    api_key = require_env("VI_API_SUBSCRIPTION_KEY")

    conn_str = require_env("AZ_STORAGE_CONNECTION_STRING")
    container = require_env("AZ_STORAGE_CONTAINER")
    video_path = require_env("VIDEO_PATH")

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"VIDEO_PATH not found: {video_path}")

    print("1) Uploading file to Blob + creating SAS URL…")
    sas_url = upload_to_blob_and_get_sas(conn_str, container, video_path)

    print("2) Generating Video Indexer access token…")
    bearer = vi_access_token(subscription_id, rg, vi_account_name)

    print("3) Starting indexing…")
    video_id = vi_upload_and_index(location, account_id, api_key, bearer, sas_url, os.path.basename(video_path))
    print("   videoId:", video_id)

    print("4) Polling until processed…")
    while True:

        data = vi_get_index(location, account_id, api_key, bearer, video_id)
        state = data.get("state")
        if state == "Failed":
            v = (data.get("videos") or [{}])[0]
            print("failureCode:", v.get("failureCode"))
            print("failureMessage:", v.get("failureMessage"))
            break
        progress = data.get("processingProgress")
        print(f"   state={state} progress={progress}")
        if state in ("Processed", "Failed"):
            break
        time.sleep(15)

    out = f"insights_{video_id}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("5) Saved:", out)
    print("Done.")

if __name__ == "__main__":
    main()
