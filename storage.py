"""
Optional Cloudflare R2 blob storage (S3-compatible).
Falls back gracefully if R2 env vars are not set.
"""
import os
from pathlib import Path

_client = None
_bucket = None


def _get_client():
    global _client, _bucket
    if _client is not None:
        return _client, _bucket

    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    bucket     = os.environ.get("R2_BUCKET_NAME")

    if not all([account_id, access_key, secret_key, bucket]):
        return None, None

    import boto3
    _client = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )
    _bucket = bucket
    return _client, _bucket


def upload(local_path: str, event_name: str, exhibitor_name: str) -> str | None:
    """Upload a local file to R2 under event/exhibitor/filename. Returns public key or None."""
    client, bucket = _get_client()
    if client is None:
        return None

    filename = Path(local_path).name
    safe_event = _slugify(event_name)
    safe_exhibitor = _slugify(exhibitor_name)
    key = f"{safe_event}/{safe_exhibitor}/{filename}"

    try:
        client.upload_file(local_path, bucket, key)
        return key
    except Exception as e:
        print(f"[R2] upload failed: {e}")
        return None


def is_configured() -> bool:
    _, bucket = _get_client()
    return bucket is not None


def _slugify(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "unknown"
