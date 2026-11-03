<p align="center">
  <a href="https://chanjoongx--realroom.us-east.modal.run">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/banner-dark.jpg">
      <source media="(prefers-color-scheme: light)" srcset="docs/banner-light.jpg">
      <img alt="Realroom. Try real furniture in a photo of your room. Keep every other pixel." src="docs/banner-light.jpg" width="100%">
    </picture>
  </a>
</p>

<h4 align="center">
  <a href="https://chanjoongx--realroom.us-east.modal.run">Live demo</a>
  &nbsp;·&nbsp;
  <a href="#how-a-run-works">How it works</a>
  &nbsp;·&nbsp;
  <a href="#demo-rooms">Demo rooms</a>
  &nbsp;·&nbsp;
  <a href="#run-it">Run it</a>
  &nbsp;·&nbsp;
  <a href="#use-your-own-catalog">Your own catalog</a>
</h4>

<p align="center">
  <a href="https://cerebralvalley.ai/e/meta-ai-developer-hackathon"><img alt="Meta Global AI Developer Hackathon 2026" src="https://img.shields.io/badge/Meta_Global_AI_Developer_Hackathon-2026-eeebe5?style=flat-square&labelColor=1f2328"></a>
  <a href="#meta-models"><img alt="Muse Spark 1.3" src="https://img.shields.io/badge/Muse_Spark-1.3-a5b8ff?style=flat-square&labelColor=1f2328"></a>
  <a href="#meta-models"><img alt="SAM 3.1" src="https://img.shields.io/badge/SAM-3.1-7fd6ff?style=flat-square&labelColor=1f2328"></a>
  <a href="#meta-models"><img alt="Muse Image 1.0" src="https://img.shields.io/badge/Muse_Image-1.0-ffb48c?style=flat-square&labelColor=1f2328"></a>
  <a href="#meta-models"><img alt="Muse Spark Open" src="https://img.shields.io/badge/Muse_Spark_Open-open_weights-d1b3ff?style=flat-square&labelColor=1f2328"></a>
  <a href="#meta-models"><img alt="Perception Encoder PE-Core B16" src="https://img.shields.io/badge/Perception_Encoder-PE--Core_B16-d1b3ff?style=flat-square&labelColor=1f2328"></a>
  <a href="https://github.com/chanjoongx/realroom/actions/workflows/ci.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/chanjoongx/realroom/ci.yml?branch=main&style=flat-square&label=tests&labelColor=1f2328"></a>
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-a8acb3?style=flat-square&labelColor=1f2328"></a>
</p>

<p align="center">
  <b>English</b>
  &nbsp;·&nbsp; <a href="docs/i18n/README.ko.md">한국어</a>
  &nbsp;·&nbsp; <a href="docs/i18n/README.ja.md">日本語</a>
  &nbsp;·&nbsp; <a href="docs/i18n/README.es.md">Español</a>
  &nbsp;·&nbsp; <a href="docs/i18n/README.pt-BR.md">Português</a>
  &nbsp;·&nbsp; <a href="docs/i18n/README.fr.md">Français</a>
  &nbsp;·&nbsp; <a href="docs/i18n/README.de.md">Deutsch</a>
  &nbsp;·&nbsp; <a href="docs/i18n/README.hi.md">हिन्दी</a>
  &nbsp;·&nbsp; <a href="docs/i18n/README.ar.md">العربية</a>
</p>

> [!NOTE]
> Realroom is an entry in the [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon), Indie Developer track, under the problem statement "Build for Your World". The agent, the web app, the catalog index and the demo rooms were all made during the building period, October 27 to November 4, 2026.
>
> The hosted app replays the three recorded runs and runs the whole agent live on an uploaded photo, up to 40 times a day. A live run takes about four to eight minutes, and the page follows the run while it works. On your own machine the same app replays the runs with no key, or runs live with your own Meta Model API key. Both ways are in [Run it](#run-it).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/built-on-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/built-on-light.png">
    <img alt="Built on Meta Model API (Muse Spark 1.3, SAM 3.1 and Muse Image 1.0 run the agent in every live run), Baseten and Fireworks AI (Muse Spark Open, open weights, tagged 3,781 and 594 catalog products) and Modal (web app, live runs, catalog jobs, Perception Encoder embeddings)" src="docs/built-on-light.png" width="100%">
  </picture>
</p>

<p align="center">
  <a href="https://chanjoongx--realroom.us-east.modal.run"><img src="docs/demo.webp" alt="The living-room run replayed in the app: the scan, the placement guide, the verify step and the before and after slider" width="100%"></a>
  <br>
  <sub>The living-room demo run, replayed in the app: scan, placement guide, verify, then the before and after slider.</sub>
</p>

Realroom is an agent built on Meta's models. You give it a room photo and one sentence about what you want. Muse Spark 1.3 reads the room and plans what to add or replace, and SAM 3.1 outlines every object. The agent searches 4,375 real products for pieces whose listed dimensions fit the space and ranks them by their Muse Spark Open descriptions and by Meta's Perception Encoder. Spark picks a set that works together, and Muse Image 1.0 draws it into the photo. Then the agent checks its own work: SAM 3.1 finds each product again in the render, Muse Spark 1.3 compares it with its catalog photo in three separate checks, and a product that came out wrong is redrawn. Only the new products, their shadows and the spots that were cleared come from the render. Every other pixel is copied back from your original photo, and the page lets you check that count in your own browser.

Try it at [chanjoongx--realroom.us-east.modal.run](https://chanjoongx--realroom.us-east.modal.run). The demo rooms replay recorded runs, and the upload box runs the whole agent on your own photo.

## Why

Room makeover generators draw a pleasant room, but the sofa in it is not one you can buy, nobody checked that it fits the wall, and the generator quietly repaints the walls, the floor and the view out of the window. If you rent, or you are shopping for one sofa, that picture answers the wrong question. Realroom keeps the room you have and changes only what you would actually buy. A furniture store can plug in its own catalog; the demo uses Amazon Berkeley Objects, a public set of real Amazon listings.

## Demo rooms

![Three demo rooms before and after](docs/rooms.jpg)

The demo rooms were generated with Muse Image 1.0, so this repository holds no private photos. Each row is one recorded run. The numbers come from that run's own `run.json` and `ledger.jsonl` in [`demo-runs/`](demo-runs).

| Room | Brief | Products | Matched | Untouched pixels | Time | Cost |
|---|---|---|---|---|---|---|
| Living room, 2005 apartment | "The old dark sofa makes the room feel gloomy. Make it brighter and cozier without painting or touching the floor." | sofa, rug, floor lamp | 3 of 3, the floor lamp after one round of redraws | 68.7% | 433&nbsp;s | $0.39 |
| Empty room, move-in day | "Move-in day. I work from home and want a calm living room where two friends can sit: something to sit on, a rug, a small table and warm evening light." | sofa, rug, floor lamp, coffee table, planter | 5 of 5, the rug and the floor lamp after one round of redraws each | 63.6% | 384&nbsp;s | $0.58 |
| Studio, first apartment | "...I still sleep on a mattress on the floor and the moving boxes are everywhere. Make it feel like a calm, grown-up bedroom without painting." | bedding, rug, wall art | 3 of 3, the bedding after one round of redraws | 67.5% | 477&nbsp;s | $0.40 |

"Matched" counts a product when at least two of three separate Muse Spark 1.3 checks of its final pixels say match. Each check puts a close crop of the product next to its catalog photo, and the app lists all three verdicts. Costs are estimates from Meta's published prices, logged per call. The recorded runs drew three candidates and could redraw up to two products. Live runs draw two and redraw at most one, so they cost less, about $0.2 to $0.5.

## How a run works

```mermaid
flowchart LR
    A(["Room photo<br>and one sentence"]) --> B["Scan and plan<br>Muse Spark 1.3 · SAM 3.1"]
    B --> C["Search and pick<br>4,375 products"]
    C --> D["Render candidates<br>Muse Image 1.0"]
    D --> E("Verify each product<br>SAM 3.1 · Muse Spark 1.3")
    E -- partial --> F["Redraw it larger<br>Muse Image 1.0"]
    F --> E
    E -- checked --> G(["Composite<br>original pixels elsewhere"])
```

| Step | Models | What happens |
|---|---|---|
| Scan and plan | Muse Spark 1.3, SAM 3.1 | One Spark request returns schema-constrained JSON: every movable object with a box and a decision (keep, remove or replace), the fixed structure, the room's size and light, and a plan of slots (kind, add or replace, target box, size range in cm, look, color family). SAM 3.1 turns the boxes into exact masks with concept prompts, and with box prompts for objects the concepts miss. |
| Search | catalog index, PE-Core B16 | For each slot: products of that kind, then only those whose real dimensions fit the slot's size range, ranked by text similarity to the Muse Spark Open description (45%) and Perception Encoder text-to-image similarity (55%). 24 per slot. The Perception Encoder runs in the app's own server, so this step makes no API call. |
| Pick | Muse Spark 1.3 | Spark sees the room and a numbered sheet of candidates for every slot in a single request, so it chooses a set that works together. |
| Render | Muse Image 1.0 | One `/images/edits` call with the room, a placement guide and the real product photos returns two or three candidates. The model's web and image search tools are switched off. |
| Verify | SAM 3.1, Muse Spark 1.3 | Each candidate is aligned to the original (ECC homography) and color-matched. SAM 3.1 finds every new product again, and Spark judges each product crop against its catalog photo and the scene as a whole. The best candidate is kept. Then Spark checks each of its products three more times, each check a separate request with a close crop next to the catalog photo. Spark's verdict on the same pixels can change from one request to the next, so a product counts as matched only when at least two of the three say match. |
| Fix | Muse Image 1.0, SAM 3.1, Muse Spark 1.3 | A product the checks call partial is cut out with its surroundings and redrawn larger against its catalog photo, two tries per round and up to two rounds. A product the checks cannot find at its planned spot is first looked for in the whole photo, because the render sometimes draws one somewhere else; only if it is not there is it added at its planned spot, the same way. A try replaces the current version only if Spark scores it higher, and Spark's notes steer the next round. A redrawn product, and any product whose own pixels a later step changed, is checked three times again on the finished photo. Clutter the render left behind gets its own pass, checked with SAM 3.1. If anything changed, Spark judges the finished room again. |
| Composite | none | The result takes from the render only the product masks, a thin fringe around them, contact shadows and the footprints of removed objects. Every other pixel is the original. |

The web app replays each run from its event log: the scan with every outline, the search funnel, the pick sheets, all render candidates, each fix with its before and after, and the final layers. Each step's header shows the ID of the model it called.

![The verify step of the living-room run](docs/verify.jpg)

Above is the verify step of the living-room run. Muse Image drew three candidates, Spark scored all three products in each, and candidate 1 was kept. Its floor lamp scored 5 of 10, and three separate checks gave it 6 each, so it went on to the fix step and came back at 9.

## Meta models

| Model | Access | Used for |
|---|---|---|
| `muse-spark-1.3` | Meta Model API | reading the room and planning, picking products, judging every product and scene |
| `sam-3.1` | Meta Model API | masks of room objects and of every rendered product, checking what clutter is left |
| `muse-image-1.0` | Meta Model API | render candidates, redraws, clutter clearing, the demo rooms |
| Muse Spark Open | Baseten, Fireworks | tags and descriptions for the whole catalog |
| `pe-core-b16-224` | open weights, in the app's server (Modal, or your machine) | Perception Encoder: matches each slot's look against the catalog photos |

When it runs, Realroom calls only the first three models, all at api.meta.ai, and runs the Perception Encoder in its own server. Muse Spark Open tagged the catalog before any run. [`backend/tests/test_meta_only.py`](backend/tests/test_meta_only.py) checks the agent's code in `backend/realroom/`: it fails if the model IDs there are not the four in this table, if the API address is not api.meta.ai, or if any string in that code holds another URL or names a common non-Meta model such as GPT, Claude, Gemini, Flux or SigLIP.

## What you can check

- `before.png` and `after.png` are lossless. In the app, the **Check it in your browser** button downloads both, compares them pixel by pixel and shows the share that is identical.
- `layers.png` colors every changed pixel as product, cleared or shadow. `masks.npz` holds every object and product mask.
- In `run.json`, each product's verdict lists the three final checks it was counted from, next to the verdict the verify or fix step gave it.
- `ledger.jsonl` lists every model call with its time and estimated cost. The Perception Encoder search is in it too, at no cost, because it runs in the app's own server.
- `drift.jpg` shows how far the raw render drifted from the original everywhere else (mean color difference dE 1.8 to 2.7 on the demo runs). Those pixels are the reason for the composite step.

## Run it

You need Python 3.12 (PyTorch 2.6.0 has no wheels for Python 3.14) and Node 22.12 or later (20.19 also works) for the web build. Install once:

```bash
python3.12 -m venv .venv
.venv/bin/pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r backend/requirements.txt
(cd web && npm ci && npm run build)
```

On Windows, create the environment with `py -3.12 -m venv .venv` and write `.venv\Scripts\` where these commands say `.venv/bin/`.

### Replay the recorded runs (no key)

```bash
.venv/bin/python backend/scripts/serve_local.py
```

Open http://127.0.0.1:8787. The demo rooms replay the runs in [`demo-runs/`](demo-runs) step by step, as on the hosted app. No model is called. The product photos those runs show are in `demo-runs/_photos`, and the upload box says that live runs are off.

Replaying needs no PyTorch. If replay is all you want, run `.venv/bin/pip install -r backend/requirements-test.txt` in place of the two `pip` lines above: it installs every Python package the replay server and the tests use. The web build is still needed.

### Live runs with your own Meta Model API key

1. Copy `.env.example` to `.env` in the repository root and fill in `MODEL_API_KEY`. git ignores `.env`.
2. Download the photos of all 4,375 products into `data/photos`. They are one file of about 730 MB on this repository's GitHub release.
   ```bash
   .venv/bin/python catalog/get_photos.py
   ```
3. Download the Perception Encoder weights once (`timm/PE-Core-B-16` on Hugging Face, about 1.8 GB). If you skip this, the first live search waits for the same download.
   ```bash
   .venv/bin/python -c "import open_clip; open_clip.create_model_and_transforms('PE-Core-B-16', pretrained='meta')"
   ```
4. Start the server with `--live`:
   ```bash
   .venv/bin/python backend/scripts/serve_local.py --live
   ```

`--live` is the only switch between replay and live. With it, the upload box runs the whole agent on your photo and the page follows the run while it works. One run costs about $0.2 to $0.5 at Meta's published prices, and its `ledger.jsonl` lists every call. The local server starts at most 10 live runs a day; `--per-day` changes that. A run that passes 12 minutes or $0.60 skips the redraws it has left and finishes with what it has.

To run the agent on one photo without the web app:

```bash
.venv/bin/python backend/scripts/run_demo.py --room room.jpg --id my-room --brief "Warmer and less cluttered" \
    --mode refresh --slots 3 --catalog catalog/index --photos data/photos --out demo-runs/my-room
```

Use `--mode furnish` for an empty room. While `serve_local.py` is running, the finished run replays at http://127.0.0.1:8787/r/my-room.

### Rebuild the catalog or host the app

To rebuild the catalog, download the ABO listings and image metadata into `data/abo/`, then run the steps in [The catalog](#the-catalog). The Modal steps need a Modal account and, for tagging, `BASETEN_API_KEY` and `FIREWORKS_API_KEY` in a Modal secret named `realroom-keys`.

The hosted version runs on Modal (`backend/modal_app.py`): one web container, the catalog and the runs on two volumes, at most three live runs at a time, 40 live runs a day and 8 an hour, three per visitor and 60 dollars of model calls in all. Uploads must be JPEG, PNG or WebP, at most 12 MB and 40 megapixels. Location and camera data are stripped before a photo is stored, and visitors are counted by a salted hash that changes every day, never by their address. A photo that shows a person or is not a room is refused and blurred at once. Live results sit at an unguessable address and are never cached. Their `after.jpg` and `after.png` carry IPTC metadata that marks them as AI-edited. Uploaded photos are deleted within 24 hours.

## The catalog

[Amazon Berkeley Objects](https://amazon-berkeley-objects.s3.us-east-1.amazonaws.com/index.html) (ABO) is a public dataset of real Amazon product listings with photos and dimensions. `catalog/` builds the Realroom catalog from it:

1. `abo_catalog.py` sorts amazon.com listings into 25 kinds (sofa, rug, floor lamp, wall art and so on), which matches 5,931 of them. It converts sizes to centimeters and drops listings with no dimensions or implausible ones, leaving 4,612 candidates.
2. `modal_catalog.py --step score` and `abo_choose.py` pick one reference photo per product, preferring a clean cutout on white.
3. `modal_catalog.py --step fetch` downloads that photo once, in Modal's cloud, and trims it.
4. `modal_catalog.py --step tag` sends every photo with its listing to **Muse Spark Open**, Meta's open-weight model, served by Baseten (Fireworks as fallback). It returns a title, colors, materials, style, a visual description, a SAM phrase, a render-risk guess, and whether the photo really shows the listed product.
5. `modal_catalog.py --step embed` embeds every photo with Meta's Perception Encoder (PE-Core B16-224) on Modal CPUs.
6. `abo_finalize.py` writes `catalog.jsonl`: 4,375 products. It dropped 104 whose photo did not show the listed product and 133 that had no finished tag when the demo runs were recorded.

Muse Spark Open tagged 3,781 of the 4,375 on Baseten and 594 on Fireworks. The tags are in [`catalog/index/catalog.jsonl`](catalog/index/catalog.jsonl). The repository holds only the product photos that the demo runs show. The full set is one file on the GitHub release (see [Run it](#run-it)), and the steps above rebuild it from ABO.

## Use your own catalog

The agent itself does not depend on ABO. It reads a catalog folder (`--catalog`) with `catalog.jsonl` and two Perception Encoder files, and a photo folder (`--photos`) with `img/<id>.jpg` and `thumb/<id>.webp` for every product. This is the first line of [`catalog/index/catalog.jsonl`](catalog/index/catalog.jsonl), shortened:

```json
{
  "id": "B075QFCHM9",
  "kind": "bed",
  "name": "Rivet York Modern Extended Platform Queen Bed, 90.2\"L, Grey",
  "brand": "Rivet",
  "size_cm": {"w": 229.0, "d": 182.9, "h": 107.5},
  "url": "https://www.amazon.com/dp/B075QFCHM9",
  "cutout": true,
  "tag": {
    "title": "Light Grey Upholstered Platform Bed",
    "matches_listing": true,
    "colors": ["light grey", "white", "light brown"],
    "color_family": "grey",
    "materials": ["polyester", "fabric", "wood"],
    "style": ["modern", "minimal", "contemporary"],
    "render_risk": "low",
    "sam_phrase": "platform bed",
    "visual_description": "Low-profile light grey polyester platform bed with tall rectangular headboard, extended surround frame and tapered wood legs shown with white mattress."
  }
}
```

- `kind` is one of the names in `KINDS` in `backend/realroom/catalog.py`. The plan asks for each slot by kind.
- `size_cm` is width, depth and height. A product reaches the shortlist only if it fits the slot's size range.
- `tag` is what Muse Spark Open wrote in step 4. The text search reads its title, colors, materials, style and description, SAM 3.1 finds the rendered product by its `sam_phrase`, and a product with `"matches_listing": false` is never offered. Without a tag the agent falls back to `name` and `kind`.
- `cutout` marks a photo of the product alone on a plain background. Search ranks those photos higher.
- `emb_pe_core_b16.npy` holds one Perception Encoder embedding per product photo, in the order of `emb_pe_core_b16_ids.json`. Step 5 writes both. Without them the search ranks by text alone.

The scripts in `catalog/` build this folder from ABO. For another product feed, steps 1 to 3 are the part to rewrite, because they turn ABO listings into rows and photos. Then point `serve_local.py` or `run_demo.py` at the new folders with `--catalog` and `--photos`.

## Tests

```bash
.venv/bin/pip install -r backend/requirements-test.txt
.venv/bin/python -m pytest backend/tests
```

The tests need no key and call no model. They replay the three recorded runs through the HTTP API and check their numbers against each ledger and this README. They push uploads through the live-run gate with a fake launcher: metadata removal, size and format limits, the per-visitor, daily and budget caps. They feed the pipeline failed and malformed model replies, and they check that every model the app calls is a Meta model reached only at api.meta.ai. GitHub Actions runs them on every push, with the web typecheck and build.

## Repository layout

```text
backend/
  realroom/      the agent: scan.py, pick.py, render.py, verify.py, composite.py and pipeline.py,
                 catalog.py (search), meta.py (Meta Model API clients), server.py (API and site)
  scripts/       serve_local.py, run_demo.py, make_rooms.py (the demo rooms), pack_demo_photos.py
  tests/         the test suite (no key, no network calls)
  modal_app.py   the hosted app on Modal
catalog/         the catalog build from ABO, steps 1 to 6, plus get_photos.py and pack_photos.py
  index/         catalog.jsonl (4,375 products) and the Perception Encoder embeddings
demo-runs/       the three recorded runs (run.json, ledger.jsonl, masks and images) and their product photos
web/             the React app (Vite): the home page, the run viewer and the pixel check
docs/            README images and translations
```

## Limits

- Subtle textures and colors are hard for the image model. Rugs with a fine weave often come out too bold, and bedding often comes out darker or grayer than its catalog photo. The fix loop redraws the weakest products (one per hosted run, two per recorded run), and Spark's verdicts on the same pixels vary from one request to the next, so a product counts as matched only when at least two of three separate checks say match.
- The render sometimes puts a product away from its planned spot, or leaves it out. When the checks find nothing at the spot, Spark looks for the product in the whole photo first, and the fix step adds it only if it is not there.
- The plan changes between runs of the same brief, because Spark plans and picks again each time.
- Where a removed object stood, the redrawn floor can meet the original floor along a faint seam.
- The listings come from a dataset published in 2021, so there are no prices and some product links no longer open.
- Spark's room size is an estimate from things like door and bed sizes. It sets the size range for the search, but the render is not a measurement.

## Credits

- Product photos and data: Amazon Berkeley Objects by **Amazon.com**, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Dataset by Matthieu Guillaumin, Thomas Dideriksen, Kenan Deng and Himanshu Arora (Amazon.com), Jasmine Collins and Jitendra Malik (UC Berkeley). Changes: photos were trimmed and resized, products were tagged by Muse Spark Open, and products are redrawn into room photos by Muse Image 1.0. Realroom is not affiliated with or endorsed by Amazon.
- Compute: Meta Model API credits from Meta, and credits from the hackathon's inference partners Baseten, Fireworks AI and Modal.
- Built with Muse Spark Open. Its weights are released under the [Muse Model License](https://huggingface.co/meta-models/Muse-Spark-Open-816B-A42B), and Realroom calls the model through Baseten and Fireworks AI to tag the catalog.
- [Perception Encoder](https://github.com/facebookresearch/perception_models) (PE-Core B16-224) by Meta FAIR, Apache 2.0, loaded with [OpenCLIP](https://github.com/mlfoundations/open_clip) from the `timm/PE-Core-B-16` weights.
- Python libraries: FastAPI (MIT), Uvicorn (BSD-3-Clause), NumPy and SciPy (BSD), OpenCV (Apache 2.0), Pillow (MIT-CMU), scikit-image (BSD-3-Clause), PyTorch and torchvision (BSD-3-Clause), OpenCLIP (MIT), timm (Apache 2.0), Requests (Apache 2.0), python-multipart (Apache 2.0), the Modal client (Apache 2.0) and Meta's `meta-sam-parser` (SAM License). The OpenAI Python SDK (Apache 2.0) is the client Meta's API docs show for the Meta Model API; here it only talks to api.meta.ai.
- Web libraries: React, Vite and Motion (MIT), Lucide icons (ISC) and TypeScript (Apache 2.0).
- Fonts: Geist and Geist Mono (Vercel) and Instrument Serif, all under the SIL Open Font License.
- The demo rooms were generated with Muse Image 1.0.

Built by [Chanjoong Kim](https://github.com/chanjoongx), an AI student at Kookmin University in Seoul. Code: MIT, see [LICENSE](LICENSE).
