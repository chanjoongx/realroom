"""Make the demo rooms with Muse Image 1.0. Every demo room in Realroom is AI-generated and labelled so.

The rooms must look like unstaged phone snapshots of ordinary homes, otherwise the makeover test
would be too easy (clean showroom photos hide alignment and lighting problems).

usage: python backend/scripts/make_rooms.py --only E1 --n 2 --out <dir>
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from realroom import imgutil, meta  # noqa: E402

TAIL = (" Realistic smartphone photo look: slight sensor noise, mild wide-angle distortion near the edges, "
        "slightly uneven white balance between daylight and indoor light, ordinary dynamic range. "
        "Not staged, not a designer interior, not a 3D render, not an advertisement. "
        "No people, no pets, no readable text, no logos, no brand names, screens switched off.")

ROOMS = {
    "R1": ("Studio apartment, Seoul",
           "A candid, unstaged smartphone photo of an ordinary small Korean studio apartment (a 'one-room', about 20 "
           "square meters) in Seoul, snapped by the tenant to show a friend. Shot handheld at eye level from the "
           "entrance corner with the phone's main 1x camera, landscape, the whole room visible. Plain off-white "
           "wallpaper with a faint texture, light oak-look vinyl flooring, one window with a white roller blind half "
           "down letting in soft afternoon daylight, a flat square white LED ceiling light. A single bed against the "
           "right wall with plain grey bedding left slightly messy, a small white laminate desk with a closed laptop "
           "and a cheap black mesh office chair, a white metal clothes drying rack with a few T-shirts, two cardboard "
           "moving boxes in a corner, a narrow white wardrobe. Bare walls, bare floor with no rug, no decor, no "
           "plants, no lamps." + TAIL),
    "R2": ("Living room, 2005 apartment",
           "A candid, unstaged smartphone photo of the living room in an older Korean apartment built around 2005, "
           "taken by the resident. Shot handheld at eye level from one corner with the phone's main 1x camera, "
           "landscape. Ivory wallpaper with a subtle damask pattern, glossy light wood laminate floor, a wide window "
           "with sheer white lace curtains and daylight coming in. A dated dark brown fabric three-seat sofa against "
           "the long wall with a folded fleece blanket on it, a small glass-top coffee table with a remote control and "
           "a tissue box, a TV that is switched off on a low glossy white TV stand along the opposite wall, a tall "
           "potted rubber plant that looks a bit tired, a plain round wall clock, otherwise bare walls. No rug, no "
           "floor lamp." + TAIL),
    "E1": ("Empty living room, move-in day",
           "A candid, unstaged smartphone photo of an empty living room in a newly rented Korean apartment on move-in "
           "day, taken by the new tenant. Shot handheld at eye level from one corner with the phone's main 1x camera, "
           "landscape, showing two walls, the floor and a wide window. Plain white wallpaper with a faint texture, "
           "white baseboards, light oak-look laminate floor with soft reflections, a wide window with a white roller "
           "blind half up and soft overcast daylight, a flat round white LED ceiling light, one wall power outlet. "
           "The room is completely empty of furniture: only two taped cardboard moving boxes and a rolled-up "
           "foam mat on the floor near the wall. Bare walls, no curtains, no rug, no lamps, no plants." + TAIL),
    "E2": ("Empty bedroom, move-in day",
           "A candid, unstaged smartphone photo of an empty small bedroom in a Korean apartment on move-in day, taken "
           "by the new tenant from the doorway with the phone's main 1x camera, landscape, the whole room visible. "
           "Light warm-grey wallpaper, light wood laminate floor, a window with a white roller blind and afternoon "
           "daylight, a flat square white LED ceiling light, a white built-in wardrobe on the left wall. No furniture "
           "except one cardboard moving box and a suitcase on the floor. Bare walls, no rug, no lamps." + TAIL),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="E1")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--size", default="1536x1152")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ledger = meta.Ledger(out / "ledger.jsonl")
    wanted = [r.strip() for r in args.only.split(",") if r.strip()]

    def run(rid: str):
        title, prompt = ROOMS[rid]
        imgs = meta.image_generate(ledger, f"room:{rid}", prompt, size=args.size, n=args.n)
        names = []
        for i, data in enumerate(imgs):
            p = imgutil.save(imgutil.load_rgb(data), out / f"{rid}_{'abcdefgh'[i]}.png")
            names.append(p.name)
        return rid, names

    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for rid, names in pool.map(run, wanted):
            print(rid, names)
    (out / "rooms.json").write_text(json.dumps({k: {"title": ROOMS[k][0], "prompt": ROOMS[k][1]} for k in ROOMS},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
    print("ledger USD", ledger.total_usd())


if __name__ == "__main__":
    main()
