"""
Cloudflare R2 / S3-compatible storage adapter for Hermes Agent.

Replaces local-disk skill & memory storage with R2 when configured.
Set env vars: HERMES_R2_ENDPOINT, HERMES_R2_ACCESS_KEY_ID,
HERMES_R2_SECRET_ACCESS_KEY, HERMES_R2_BUCKET, HERMES_R2_REGION=auto
"""
from __future__ import annotations
import logging, os, io, json
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def _r2_enabled() -> bool:
    return all(os.environ.get(v, "").strip() for v in (
        "HERMES_R2_ENDPOINT", "HERMES_R2_ACCESS_KEY_ID", "HERMES_R2_SECRET_ACCESS_KEY", "HERMES_R2_BUCKET"))

def _get_s3_client():
    try: import boto3
    except ImportError: raise ImportError("boto3 required for R2. Install: pip install boto3") from None
    return boto3.client("s3",
        endpoint_url=os.environ["HERMES_R2_ENDPOINT"],
        aws_access_key_id=os.environ["HERMES_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["HERMES_R2_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("HERMES_R2_REGION", "auto"))

class R2StorageAdapter:
    def __init__(self):
        self._bucket = os.environ["HERMES_R2_BUCKET"]
        self._client = None

    @property
    def client(self):
        if self._client is None: self._client = _get_s3_client()
        return self._client

    def list_skills(self) -> list[str]:
        try:
            resp = self.client.list_objects_v2(Bucket=self._bucket, Prefix="skills/", Delimiter="/")
            return [p["Prefix"].removeprefix("skills/").rstrip("/") for p in resp.get("CommonPrefixes", [])]
        except Exception: logger.exception("R2 list_skills"); return []

    def read_skill_file(self, skill_name: str, path: str) -> Optional[str]:
        key = f"skills/{skill_name}/{path}"
        try:
            resp = self.client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read().decode("utf-8")
        except self.client.exceptions.NoSuchKey: return None
        except Exception: logger.exception("R2 read: %s", key); return None

    def write_skill_file(self, skill_name: str, path: str, content: str) -> bool:
        key = f"skills/{skill_name}/{path}"
        try:
            self.client.put_object(Bucket=self._bucket, Key=key, Body=content.encode(), ContentType="text/plain; charset=utf-8")
            return True
        except Exception: logger.exception("R2 write: %s", key); return False

    def delete_skill(self, skill_name: str) -> bool:
        prefix = f"skills/{skill_name}/"
        try:
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=prefix):
                objs = page.get("Contents", [])
                if objs: self.client.delete_objects(Bucket=self._bucket, Delete={"Objects": [{"Key": o["Key"]} for o in objs]})
            return True
        except Exception: logger.exception("R2 delete: %s", skill_name); return False

    def read_memory_file(self, path: str) -> Optional[str]:
        key = f"memory/{path}"
        try: return self.client.get_object(Bucket=self._bucket, Key=key)["Body"].read().decode("utf-8")
        except self.client.exceptions.NoSuchKey: return None
        except Exception: logger.exception("R2 mem read: %s", key); return None

    def write_memory_file(self, path: str, content: str) -> bool:
        key = f"memory/{path}"
        try:
            self.client.put_object(Bucket=self._bucket, Key=key, Body=content.encode(), ContentType="text/plain; charset=utf-8")
            return True
        except Exception: logger.exception("R2 mem write: %s", key); return False

    def upload_artifact(self, session_id: str, filename: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        key = f"sessions/{session_id}/artifacts/{filename}"
        try:
            self.client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
            return True
        except Exception: logger.exception("R2 upload: %s", key); return False

    def get_artifact_url(self, session_id: str, filename: str, expiry: int = 3600) -> Optional[str]:
        key = f"sessions/{session_id}/artifacts/{filename}"
        try: return self.client.generate_presigned_url("get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expiry)
        except Exception: logger.exception("R2 presign: %s", key); return None

_r2_adapter: Optional[R2StorageAdapter] = None

def get_r2_adapter() -> Optional[R2StorageAdapter]:
    global _r2_adapter
    if _r2_adapter is None and _r2_enabled():
        _r2_adapter = R2StorageAdapter()
        logger.info("R2 adapter initialized (bucket=%s)", os.environ["HERMES_R2_BUCKET"])
    return _r2_adapter

def patch_skill_loader_for_r2():
    adapter = get_r2_adapter()
    if adapter is None: return
    try: from agent import skill_utils
    except ImportError:
        logger.warning("Cannot patch skill_utils"); return
    _orig = getattr(skill_utils, "_read_skill_file_from_disk", None)
    if _orig is None:
        _orig = skill_utils.read_skill_file
        skill_utils._read_skill_file_from_disk = _orig
    def _r2_read(skill_name: str, path: str) -> Optional[str]:
        content = adapter.read_skill_file(skill_name, path)
        if content is not None: return content
        if _orig: return _orig(skill_name, path)
        return None
    skill_utils.read_skill_file = _r2_read
    logger.info("Skill loader patched for R2")

def mirror_skills_to_r2(local_skills_dir: Path):
    adapter = get_r2_adapter()
    if adapter is None:
        logger.warning("R2 not configured"); return
    skills_dir = Path(local_skills_dir)
    if not skills_dir.is_dir():
        logger.warning("Skills dir not found: %s", skills_dir); return
    count = 0
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir(): continue
        for fp in skill_dir.rglob("*"):
            if fp.is_file():
                rel = str(fp.relative_to(skill_dir))
                if adapter.write_skill_file(skill_dir.name, rel, fp.read_text()): count += 1
    logger.info("Mirrored %d skill files to R2", count)

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if not _r2_enabled():
        print("R2 not configured. Set HERMES_R2_ENDPOINT, HERMES_R2_ACCESS_KEY_ID, HERMES_R2_SECRET_ACCESS_KEY, HERMES_R2_BUCKET.")
        sys.exit(1)
    sd = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.hermes/skills")
    mirror_skills_to_r2(Path(sd))
    print("Done.")
