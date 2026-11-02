"""Pack the product photos of every catalog product into realroom-photos.zip for the GitHub release.

Only products in catalog.jsonl go in. The zip stores the files as they are (they are JPEG and WebP
already), with a NOTICE.txt that credits Amazon Berkeley Objects.

usage: python catalog/pack_photos.py --photos <dir with img/ and thumb/> --out realroom-photos.zip
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOTICE = """Realroom catalog photos

Product photos from Amazon Berkeley Objects (ABO) by Amazon.com, licensed CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/). Dataset by Matthieu Guillaumin, Thomas Dideriksen,
Kenan Deng and Himanshu Arora (Amazon.com), Jasmine Collins and Jitendra Malik (UC Berkeley).
Changes: one photo per product was chosen, its white margins were trimmed, and it was resized
(img/ at most 1024 px, thumb/ 480 px square). Realroom is not affiliated with or endorsed by Amazon.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", required=True)
    ap.add_argument("--catalog", default=str(ROOT / "catalog" / "index" / "catalog.jsonl"))
    ap.add_argument("--out", default="realroom-photos.zip")
    args = ap.parse_args()
    src = pathlib.Path(args.photos)
    ids = [json.loads(l)["id"] for l in pathlib.Path(args.catalog).read_text(encoding="utf-8").splitlines() if l.strip()]
    missing = []
    with zipfile.ZipFile(args.out, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("NOTICE.txt", NOTICE)
        for pid in ids:
            for which, ext in (("img", "jpg"), ("thumb", "webp")):
                f = src / which / f"{pid}.{ext}"
                if f.exists():
                    z.write(f, f"{which}/{pid}.{ext}")
                else:
                    missing.append(f"{which}/{pid}")
    if missing:
        raise SystemExit(f"missing photos: {missing[:8]} ({len(missing)} in all)")
    h = hashlib.sha256(pathlib.Path(args.out).read_bytes()).hexdigest()
    print(f"{len(ids)} products, {pathlib.Path(args.out).stat().st_size / 1e6:,.1f} MB, sha256 {h}")


if __name__ == "__main__":
    main()
