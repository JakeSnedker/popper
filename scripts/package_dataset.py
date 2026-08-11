"""Package the canonical dataset as ONE archive for a remote operator, with a hash.

Why one file: a Google Drive / OneDrive *folder* download is zipped on the fly,
cannot be resumed, and a broken resume produces a truncated archive that looks
fine until the dataset hash fails halfway through someone else's evening. A
single stored file resumes properly and is verifiable before it is extracted.

Why stored, not deflated: parquet is already compressed. Re-compressing spends
minutes of CPU to save low single-digit percent.

    python scripts/package_dataset.py                     # -> ~/Downloads
    python scripts/package_dataset.py --out D:\\share      # somewhere else
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
import time
import zipfile

from popper.config import settings
from popper.data.inventory import human, inspect

CHUNK = 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def dataset_dir() -> Path:
    configured = settings.dataset_dir.strip()
    root = Path(__file__).resolve().parent.parent
    path = Path(configured).expanduser() if configured else root / "data" / "ftmo-us30-v1"
    return path if path.is_absolute() else root / path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(Path.home() / "Downloads"),
                        help="directory to write the archive into (default: ~/Downloads)")
    parser.add_argument("--name", default=None, help="archive filename (default: <dataset>.zip)")
    args = parser.parse_args()

    source = dataset_dir()
    report = inspect(source)
    if not report["research_ready"]:
        print(f"Refusing to package an incomplete dataset at {source}")
        print("  manifest:", "present" if report["manifest"] else "MISSING")
        for timeframe, item in report["bars"].items():
            print(f"  bars/{timeframe}:", "present" if item["present"] else "MISSING")
        return 1

    destination = Path(args.out).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / (args.name or f"{source.name}.zip")

    files = sorted(p for p in source.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    print(f"Packaging {len(files)} files ({human(total)}) from {source}")
    print(f"  -> {archive}")
    if not report["ticks"]["present"]:
        print("  NOTE: this copy has no ticks/ directory; the operator will not be able")
        print("        to run the full-hash verification against the canonical manifest.")

    started = time.time()
    written = 0
    # Stored, not deflated: see module docstring.
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
        for index, path in enumerate(files, 1):
            bundle.write(path, arcname=str(path.relative_to(source.parent)))
            written += path.stat().st_size
            if index % 200 == 0 or index == len(files):
                percent = 100 * written / total if total else 100
                print(f"  {index}/{len(files)} files  {percent:5.1f}%", flush=True)

    size = archive.stat().st_size
    print(f"\nArchive written in {time.time() - started:.0f}s ({human(size)})")
    print("Hashing the archive...", flush=True)
    digest = sha256(archive)

    sidecar = archive.with_suffix(archive.suffix + ".sha256.txt")
    sidecar.write_text(
        f"{digest} *{archive.name}\n\n"
        f"dataset      : {source.name}\n"
        f"files        : {len(files)}\n"
        f"archive bytes: {size}\n"
        f"packaged (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
        "Verify on Windows before extracting:\n"
        f"  Get-FileHash -Algorithm SHA256 '{archive.name}'\n"
        "The hash must match exactly. If it does not, the download truncated -\n"
        "download it again rather than extracting it.\n",
        encoding="utf-8",
    )

    print(f"\nSHA-256: {digest}")
    print(f"Written : {sidecar}")
    print("\nSend the operator: the archive link, this SHA-256, and the release tag.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
