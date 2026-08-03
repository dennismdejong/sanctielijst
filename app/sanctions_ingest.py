import os
from pathlib import Path

from .pep_ingest import fetch_index, list_collection_datasets, refresh_collection

SANCTIONS_COLLECTION = "sanctions"
EXCLUDE_DATASETS = ("eu_fsf",)


def default_root() -> Path:
    return Path(os.environ.get("SANCTIONS_DATA_DIR", "data/sanctions"))


def list_sanctions_datasets(index: dict) -> list[dict]:
    return list_collection_datasets(index, SANCTIONS_COLLECTION, exclude=EXCLUDE_DATASETS)


def refresh_sanctions(
    root_dir: Path,
    *,
    index: dict | None = None,
    force: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    logger=None,
) -> dict:
    return refresh_collection(
        root_dir,
        SANCTIONS_COLLECTION,
        index=index,
        force=force,
        dry_run=dry_run,
        limit=limit,
        logger=logger,
        exclude=EXCLUDE_DATASETS,
    )
