"""
Shared configuration and publishing utilities for the MUR SST workflow.

The active MUR workflow uses this module to load runtime configuration and
publish generated NetCDF products to the configured production Google Cloud
Storage bucket. The Cloud Run container provides the config path through the
ROYLIB_CONFIG environment variable, defaulting to /app/config/config.yml.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from google.cloud import storage


DEFAULT_CONFIG_PATH = "/app/config/config.yml"


def _expand_once(value: Any, cfg: dict[str, Any]) -> Any:
    """
    Expand simple ${KEY} references in a string using values from the config.

    Only string values are expanded. Non-string values are returned unchanged.
    """
    if not isinstance(value, str):
        return value

    out = value
    for key, replacement in cfg.items():
        if isinstance(replacement, (str, int, float)):
            out = out.replace("${" + key + "}", str(replacement))
    return out


def _expand_tree(obj: Any, cfg: dict[str, Any]) -> Any:
    """
    Recursively expand simple ${KEY} references in nested config objects.
    """
    if isinstance(obj, dict):
        return {key: _expand_tree(value, cfg) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_expand_tree(value, cfg) for value in obj]
    return _expand_once(obj, cfg)


def _inject_dirs(cfg: dict[str, Any]) -> dict[str, Any]:
    """
    Flatten values from the optional DIRS config block into the top-level config.

    The workflow historically accessed several directory settings directly from
    CFG. This helper keeps that interface while allowing config.yml to group
    directory settings under DIRS.
    """
    dirs = cfg.get("DIRS", {}) or {}
    for key, value in dirs.items():
        cfg[key] = value
    return cfg


def load_cfg(path: str | os.PathLike[str]) -> dict[str, Any]:
    """
    Load and normalize the workflow YAML configuration.

    Parameters
    ----------
    path
        Path to the YAML configuration file.

    Returns
    -------
    dict
        Configuration dictionary with DIRS values injected at top level and
        simple ${KEY} references expanded.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    cfg = _inject_dirs(cfg)
    cfg = _expand_tree(cfg, cfg)
    return cfg


CFG = load_cfg(os.environ.get("ROYLIB_CONFIG", DEFAULT_CONFIG_PATH))


def P(key: str) -> Path:
    """
    Return a config value as a pathlib.Path.

    Parameters
    ----------
    key
        Config key to read from CFG.

    Returns
    -------
    pathlib.Path
        The config value converted to a Path.
    """
    return Path(CFG[key])


def list_bucket_content(bucket_name: str, dir_path: str) -> list[str]:
    """
    List object names in a GCS bucket under a prefix.

    Parameters
    ----------
    bucket_name
        Name of the GCS bucket, without a ``gs://`` prefix.
    dir_path
        Bucket-relative prefix to list.

    Returns
    -------
    list[str]
        Object names under the requested prefix.
    """
    client = storage.Client()
    blobs = client.bucket(bucket_name).list_blobs(prefix=dir_path)
    return [blob.name for blob in blobs]


def upload_file_to_gcs(local_path: str | Path, bucket_name: str, blob_name: str) -> None:
    """
    Upload a local file to a GCS object.

    Parameters
    ----------
    local_path
        Local file to upload.
    bucket_name
        Destination GCS bucket name, without a ``gs://`` prefix.
    blob_name
        Destination object name inside the bucket.
    """
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"Local file not found: {local_path}")

    bucket = storage.Client().bucket(bucket_name)
    bucket.blob(blob_name).upload_from_filename(str(local_path))
    print(f"[INFO] Uploaded {local_path.name} -> gs://{bucket_name}/{blob_name}")


def send_to_servers(
    nc_src_path: str | Path,
    dst_dir: str | Path,
    interval: str = "1",
) -> None:
    """
    Upload a NetCDF product to the configured production GCS bucket.

    The destination path is constructed from:

    - ``PUBLISH_TARGETS.prod.root``
    - the product-specific destination directory
    - the configured interval directory format
    - the source filename

    Parameters
    ----------
    nc_src_path
        Local NetCDF file to publish.
    dst_dir
        Product-specific destination directory below the production root.
    interval
        Interval token used with ``PUBLISH_TARGETS.prod.interval_fmt``.
        Typical values are ``"1"`` for daily products and ``"m"`` for monthly
        products.
    """
    nc_src_path = Path(nc_src_path)
    if not nc_src_path.exists():
        raise FileNotFoundError(f"Local file not found: {nc_src_path}")

    pub_cfg = CFG["PUBLISH_TARGETS"]
    prod_cfg = pub_cfg["prod"]

    prod_bucket_name = prod_cfg["bucket"]
    prod_root = Path(str(prod_cfg["root"]).strip("/"))
    dst_dir = Path(str(dst_dir).strip("/"))
    interval_dir = Path(prod_cfg.get("interval_fmt", "{interval}day").format(interval=interval))

    remote_dir = prod_root / dst_dir / interval_dir
    blob_name = remote_dir / nc_src_path.name

    upload_file_to_gcs(
        local_path=nc_src_path,
        bucket_name=prod_bucket_name,
        blob_name=str(blob_name),
    )