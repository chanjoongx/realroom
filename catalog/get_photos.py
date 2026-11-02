"""Download the catalog's product photos for live runs (Amazon Berkeley Objects, Amazon.com, CC BY 4.0).

The photos are about 730 MB, too big for the repository, so they are one file on the repository's
GitHub release. This script downloads it, checks its SHA-256 and unpacks img/, thumb/ and NOTICE.txt
into data/photos, where backend/scripts/serve_local.py looks for them. Replaying the recorded demo
runs does not need them.

usage: python catalog/get_photos.py [--out data/photos] [--zip realroom-photos.zip]
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import zipfile

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
URL = "https://github.com/chanjoongx/realroom/releases/download/catalog-photos/realroom-photos.zip"
SHA256 = "c0e26ff363369279bbebe6772b9c76acd2473c03e580123385d7d1861d1c9c42"


def download(zpath: pathlib.Path) -> str:
    h = hashlib.sha256()
    with requests.get(URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(zpath, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total:
                    print(f"\r{done / 1e6:,.0f} / {total / 1e6:,.0f} MB", end="", flush=True)
    print()
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "photos"))
    ap.add_argument("--zip", default=None, help="use a realroom-photos.zip you already downloaded")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.zip:
        zpath = pathlib.Path(args.zip)
        h = hashlib.sha256()
        with open(zpath, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
    else:
        zpath = out.parent / "realroom-photos.zip"
        digest = download(zpath)
    if digest != SHA256:
        raise SystemExit(f"SHA-256 mismatch: got {digest}, expected {SHA256}")
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist()
                 if (n == "NOTICE.txt" or n.startswith(("img/", "thumb/"))) and ".." not in n]
        for n in names:
            z.extract(n, out)
    if not args.zip:
        zpath.unlink()
    n_img = len(list((out / "img").glob("*.jpg")))
    n_thumb = len(list((out / "thumb").glob("*.webp")))
    print(f"{n_img} photos and {n_thumb} thumbnails in {out}")


if __name__ == "__main__":
    main()
