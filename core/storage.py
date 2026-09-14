"""Storage abstraction so Logic Apps can pass blob locations later.

LocalStorage works today (CLI + tests). AzureBlobStorage is a wired-but-optional
placeholder: fill in the two methods when you connect the Storage account. Selection
is by STORAGE_BACKEND env var — no workflow code changes needed.
"""
from __future__ import annotations
import os
import base64
import tempfile
import pandas as pd

from .settings import settings


class LocalStorage:
    def __init__(self, output_dir: str | None = None):
        self.output_dir = output_dir or settings.LOCAL_OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)

    def fetch_input(self, ref: dict) -> str:
        """ref = {'path': ...} or {'content_base64': ..., 'filename': ...}. Returns local path."""
        if ref.get("path"):
            return ref["path"]
        if ref.get("content_base64"):
            data = base64.b64decode(ref["content_base64"])
            fd, tmp = tempfile.mkstemp(suffix="_" + ref.get("filename", "input.xlsx"))
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            return tmp
        raise ValueError("input ref must contain 'path' or 'content_base64'")

    def write_csv(self, df: pd.DataFrame, name: str, run_id: str) -> str:
        run_dir = os.path.join(self.output_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)
        dest = os.path.join(run_dir, name)
        df.to_csv(dest, index=False)
        return dest


class AzureBlobStorage:
    """Placeholder. Requires azure-storage-blob + AZURE_STORAGE_CONNECTION_STRING.

    Implement fetch_input() to download the input blob to a temp file, and write_csv()
    to upload the CSV to OUTPUT_CONTAINER/<run_id>/<name> and return the blob URL.
    """

    def __init__(self):
        self.conn = settings.AZURE_STORAGE_CONNECTION_STRING
        self.container = settings.OUTPUT_CONTAINER
        if not self.conn:
            raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING not set")

    def fetch_input(self, ref: dict) -> str:  # pragma: no cover - placeholder
        # from azure.storage.blob import BlobClient
        # blob = BlobClient.from_blob_url(ref["blob_url"]) or from container+name
        # download to a temp path and return it
        raise NotImplementedError("Wire up Azure Blob download here.")

    def write_csv(self, df: pd.DataFrame, name: str, run_id: str) -> str:  # pragma: no cover
        # from azure.storage.blob import BlobServiceClient
        # upload df.to_csv(...) to f"{run_id}/{name}" and return the blob URL
        raise NotImplementedError("Wire up Azure Blob upload here.")


def get_storage(output_dir: str | None = None):
    if settings.STORAGE_BACKEND == "azure_blob":
        return AzureBlobStorage()
    return LocalStorage(output_dir)
