"""Mask-guarded compositing: align a generated edit to the original photo, measure how much
of the untouched room the model changed, and paste back only the edited regions.

The renter rule (walls, floor, windows and fixtures stay) is enforced here in pixels, not only
in the prompt: everything outside the edit mask comes from the original photo.
"""
from __future__ import annotations

import cv2
import numpy as np
from skimage.color import deltaE_cie76, rgb2lab
from scipy import ndimage as ndi
from skimage.metrics import structural_similarity


def diag(h: int, w: int) -> float:
    return float(np.hypot(h, w))


def boxes_mask(h: int, w: int, boxes: list[tuple[int, int, int, int]], grow: float = 0.0) -> np.ndarray:
    m = np.zeros((h, w), dtype=bool)
    g = int(grow * diag(h, w))
    for x1, y1, x2, y2 in boxes:
        m[max(0, y1 - g):min(h, y2 + g + 1), max(0, x1 - g):min(w, x2 + g + 1)] = True
    return m


def estimate_alignment(orig: np.ndarray, out: np.ndarray, static: np.ndarray) -> dict:
    """ECC homography from the edit to the original, fitted on regions that should not change."""
    h, w = orig.shape[:2]
    s = 768 / max(h, w)
    small = (round(w * s), round(h * s))
    a = cv2.cvtColor(cv2.resize(orig, small, interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    b = cv2.cvtColor(cv2.resize(out, small, interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    msk = cv2.resize(static.astype(np.uint8) * 255, small, interpolation=cv2.INTER_NEAREST)
    warp = np.eye(3, dtype=np.float32)
    try:
        cc, warp = cv2.findTransformECC(a, b, warp, cv2.MOTION_HOMOGRAPHY,
                                        (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-7),
                                        msk, 5)
        ok = True
    except cv2.error:
        cc, ok = 0.0, False
    scale = np.diag([1 / s, 1 / s, 1]).astype(np.float64)
    full = scale @ warp.astype(np.float64) @ np.linalg.inv(scale)
    corners = np.array([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]], dtype=np.float64).T
    moved = full @ corners
    moved = moved[:2] / moved[2]
    shift = float(np.max(np.linalg.norm(moved - corners[:2], axis=0)))
    return {"ok": ok, "cc": float(cc), "H": full, "max_corner_shift_px": round(shift, 2)}


def apply_alignment(out: np.ndarray, H: np.ndarray, width: int, height: int) -> np.ndarray:
    return cv2.warpPerspective(out, H, (width, height), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_REFLECT)


def refine_alignment(orig: np.ndarray, out: np.ndarray, static: np.ndarray) -> tuple[np.ndarray, dict]:
    """Second alignment pass after the homography. The generator often redraws the frame with a
    slightly different lens, so a door frame near the edge can still be a few pixels off, and pasting
    there would show a ghost of it. Dense optical flow is measured where the photo should not change
    and has texture, smoothed, spread over the rest of the frame, and the output is warped by it.
    The result is kept only if the regions that should not change match the original better."""
    h, w = orig.shape[:2]
    d = diag(h, w)
    s = 896 / max(h, w)
    small = (round(w * s), round(h * s))
    a = cv2.cvtColor(cv2.resize(orig, small, interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
    b = cv2.cvtColor(cv2.resize(out, small, interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
    flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 5, 25, 5, 7, 1.5, cv2.OPTFLOW_FARNEBACK_GAUSSIAN)
    gx, gy = np.meshgrid(np.arange(small[0], dtype=np.float32), np.arange(small[1], dtype=np.float32))
    warped = cv2.remap(b, gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    resid = np.abs(warped.astype(np.float32) - a.astype(np.float32))
    st = cv2.resize(static.astype(np.uint8), small, interpolation=cv2.INTER_NEAREST) > 0
    mag = np.hypot(flow[..., 0], flow[..., 1])
    grad = np.hypot(cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3))
    wgt = (st & (resid < 10) & (mag < 0.03 * d * s) & (grad > 20)).astype(np.float32)
    info = {"refined": False, "coverage": round(float(wgt.mean()), 4)}
    if wgt.mean() < 0.01:
        return out, info
    field = pull_push(flow, wgt)
    field = cv2.GaussianBlur(field, (0, 0), max(2.0, 0.012 * d * s))
    full = cv2.resize(field, (w, h), interpolation=cv2.INTER_LINEAR) / s
    gx2, gy2 = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    ref = cv2.remap(out, gx2 + full[..., 0], gy2 + full[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    before = float(delta_e(orig, out)[static].mean())
    after = float(delta_e(orig, ref)[static].mean())
    fm = np.hypot(full[..., 0], full[..., 1])
    info.update(static_dE_before=round(before, 3), static_dE_after=round(after, 3),
                mean_shift_px=round(float(fm.mean()), 2), max_shift_px=round(float(fm.max()), 2))
    if after < before * 0.98:
        info["refined"] = True
        return ref, info
    return out, info


def delta_e(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return deltaE_cie76(rgb2lab(a.astype(np.float32) / 255), rgb2lab(b.astype(np.float32) / 255)).astype(np.float32)


def preservation(orig: np.ndarray, out: np.ndarray, static: np.ndarray) -> dict:
    """How much of the region that should stay untouched did the generator change?"""
    de = delta_e(orig, out)
    sel = de[static]
    ga = cv2.cvtColor(orig, cv2.COLOR_RGB2GRAY)
    gb = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY)
    _, ssim_map = structural_similarity(ga, gb, full=True, data_range=255)
    return {
        "static_frac": round(float(static.mean()), 3),
        "dE_mean": round(float(sel.mean()), 2),
        "dE_p95": round(float(np.percentile(sel, 95)), 2),
        "changed_frac_dE10": round(float((sel > 10).mean()), 4),
        "ssim_static": round(float(ssim_map[static].mean()), 4),
    }, de


def edit_mask(
    h: int,
    w: int,
    new_masks: list[np.ndarray],
    old_masks: list[np.ndarray],
    target_boxes: list[tuple[int, int, int, int]],
    de: np.ndarray,
    shadow_dE: float = 9.0,
) -> np.ndarray:
    """Union of new-object and removed-object masks, plus nearby changed pixels (contact
    shadows, reflections), limited to the neighbourhood of the planned targets."""
    d = diag(h, w)
    m = np.zeros((h, w), dtype=bool)
    for mm in new_masks + old_masks:
        m |= mm
    k1 = max(3, int(0.010 * d)) | 1
    m = cv2.dilate(m.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k1, k1))) > 0
    near = boxes_mask(h, w, target_boxes, grow=0.05)
    if m.any():
        ys, xs = np.where(m)
        near |= boxes_mask(h, w, [(xs.min(), ys.min(), xs.max(), ys.max())], grow=0.03)
    changed = cv2.GaussianBlur(de, (0, 0), 2.0) > shadow_dE
    changed &= near
    k2 = max(3, int(0.015 * d)) | 1
    grown = cv2.dilate(m.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k2 * 3, k2 * 3))) > 0
    m |= changed & grown
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k2, k2))) > 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
    keep = np.zeros_like(m)
    min_area = 0.0004 * h * w
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            keep |= labels == i
    return keep


def _disk(r: float) -> np.ndarray:
    k = max(3, int(r)) | 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def _dilate(m: np.ndarray, r: float) -> np.ndarray:
    return cv2.dilate(m.astype(np.uint8), _disk(r)) > 0


def to_lab(img: np.ndarray) -> np.ndarray:
    return rgb2lab(img.astype(np.float32) / 255).astype(np.float32)


def color_offset_field(orig: np.ndarray, out: np.ndarray, static: np.ndarray, sigma_frac: float = 0.06) -> np.ndarray:
    """Low-frequency colour drift of the generator (white balance, exposure), measured on pixels
    that should not have changed and interpolated into the edited areas by normalized convolution.
    Adding it to the output makes pasted regions meet the original photo without a visible seam."""
    h, w = static.shape
    s = 384 / max(h, w)
    small = (max(1, round(w * s)), max(1, round(h * s)))
    a = cv2.resize(orig.astype(np.float32), small, interpolation=cv2.INTER_AREA)
    b = cv2.resize(out.astype(np.float32), small, interpolation=cv2.INTER_AREA)
    wgt = (cv2.resize(static.astype(np.float32), small, interpolation=cv2.INTER_AREA) > 0.99).astype(np.float32)
    diff = a - b
    wgt *= (np.linalg.norm(diff, axis=2) < 25).astype(np.float32)  # ignore real, unplanned changes
    sig = sigma_frac * float(np.hypot(*small))
    num = cv2.GaussianBlur(diff * wgt[..., None], (0, 0), sig)
    den = cv2.GaussianBlur(wgt, (0, 0), sig)[..., None]
    glob = (diff * wgt[..., None]).sum((0, 1)) / max(1.0, float(wgt.sum()))
    local = num / np.maximum(den, 1e-6)
    t = np.clip(den / 0.05, 0, 1)
    field = local * t + glob * (1 - t)
    return cv2.resize(field.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)


def harmonize(orig: np.ndarray, out: np.ndarray, static: np.ndarray) -> np.ndarray:
    return np.clip(out.astype(np.float32) + color_offset_field(orig, out, static), 0, 255).astype(np.uint8)


def hull_mask(m: np.ndarray) -> np.ndarray:
    """Filled convex hull of a mask (a rug segmented only along its border becomes the whole rug)."""
    cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros(m.shape, dtype=np.uint8)
    if cs:
        cv2.fillPoly(out, [cv2.convexHull(np.concatenate(cs))], 1)
    return out > 0


def edit_mask_v2(
    orig: np.ndarray,
    out: np.ndarray,
    new_masks: list[np.ndarray],
    old_masks: list[np.ndarray],
    shadows: str = "shade",
    old_zone: np.ndarray | None = None,
    keep: np.ndarray | None = None,
) -> tuple[np.ndarray, dict]:
    """Which pixels of the (aligned, colour-matched) output replace the original photo.

    1. every new product (SAM instance masks),
    2. pixels enclosed by a product that changed (seen through a glass top, between legs),
    3. anything changed within a thin fringe of a product,
    4. contact shadows: darker but same hue, a little further out,
    5. the full footprint of removed or replaced objects, and their old shadows that went lighter.
    Anything else the generator changed (a recoloured sofa next to a cushion, redrawn wallpaper)
    stays original, so the renter rule holds in pixels.
    """
    h, w = orig.shape[:2]
    d = diag(h, w)
    new = np.zeros((h, w), dtype=bool)
    for mm in new_masks:
        new |= mm
    old = np.zeros((h, w), dtype=bool)
    for mm in old_masks:
        old |= mm
    la, lb = to_lab(orig), to_lab(out)
    de = cv2.GaussianBlur(np.linalg.norm(lb - la, axis=2), (0, 0), 1.5)
    dl = cv2.GaussianBlur(lb[..., 0] - la[..., 0], (0, 0), 2.0)
    dc = cv2.GaussianBlur(np.hypot(lb[..., 1] - la[..., 1], lb[..., 2] - la[..., 2]), (0, 0), 2.0)
    parts = {}
    parts["product"] = new.copy()
    hulls = np.zeros((h, w), dtype=bool)
    for mm in new_masks:
        if mm.any():
            hulls |= hull_mask(mm)
    parts["enclosed"] = (hulls & ~new) & (de > 6)
    shadow_like = (dl < -3) & (dc < 8)
    if shadows == "paste":
        parts["fringe"] = _dilate(new, 0.012 * d) & (de > 9)
        parts["shadow"] = _dilate(new, 0.05 * d) & (dl < -4) & (dc < 8) & (de > 5)
    else:  # shadows come from shade_layer(): only the product's own anti-aliased edge is pasted here
        parts["fringe"] = _dilate(new, 0.006 * d) & (de > 9) & ~shadow_like
    if old.any():
        # the old object's footprint, plus any sliver of it SAM missed: pixels near it that the
        # generator replaced with wall or floor (otherwise a dark remnant of an old sofa survives)
        parts["removed"] = _dilate(old, 0.01 * d) | (_dilate(old, 0.025 * d) & (de > 10) & ~shadow_like)
        parts["old_shadow"] = _dilate(old, 0.02 * d) & (dl > 6) & (dc < 8) & (de > 6)
    if old_zone is not None and old_zone.any():
        # wherever a removed object could still be (its Spark box too) and the generator changed the
        # pixels, take the generator's version: a box SAM missed must not come back from the original
        z = old_zone & (de > 8) & ~shadow_like
        if keep is not None:
            z &= ~_dilate(keep, 0.003 * d)
        parts["removed_zone"] = z
    m = np.zeros((h, w), dtype=bool)
    for v in parts.values():
        m |= v
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE, _disk(0.008 * d)) > 0
    holes = ndi.binary_fill_holes(m) & ~m
    lab_h, n_h = ndi.label(holes)
    if n_h:
        sizes = ndi.sum(holes, lab_h, range(1, n_h + 1))
        small_ids = [i + 1 for i, a in enumerate(sizes) if a < 0.002 * h * w]
        m |= np.isin(lab_h, small_ids)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
    keep = np.zeros_like(m)
    min_area = 0.0004 * h * w
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            keep |= labels == i
    keep = cv2.GaussianBlur(keep.astype(np.float32), (0, 0), max(2.0, 0.002 * d)) > 0.5  # smooth outline
    frac = {k: round(float(v.mean()), 4) for k, v in parts.items()}
    return keep, frac


def complete_mask(orig: np.ndarray, out: np.ndarray, mask: np.ndarray, near: tuple | None = None,
                  forbid: np.ndarray | None = None) -> np.ndarray:
    """Grow a SAM product mask over the parts it missed.

    SAM tends to stop at a product's thin or dark parts (the legs of a plant stand, a lamp pole) and
    leaves holes. The compositor would then show the original floor through them, or take a dark leg
    for a shadow and only darken the floor there. Pixels that changed strongly and hang together with
    the mask belong to the product; soft shadows do not (shade_layer handles those). The search stays
    near the product (`near` is its planned box) and never enters `forbid`: objects that must not
    change, objects being removed, other products.
    """
    h, w = mask.shape
    if not mask.any():
        return mask
    d = diag(h, w)
    ys, xs = np.nonzero(mask)
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    if near is not None:
        x1, y1 = min(x1, int(near[0])), min(y1, int(near[1]))
        x2, y2 = max(x2, int(near[2])), max(y2, int(near[3]))
    bw, bh = x2 - x1, y2 - y1
    pad = 0.012 * d
    wx1, wx2 = max(0, int(x1 - 0.15 * bw - pad)), min(w, int(x2 + 0.15 * bw + pad))
    wy1, wy2 = max(0, int(y1 - 0.12 * bh - pad)), min(h, int(y2 + 0.3 * bh + pad))
    la, lb = to_lab(orig[wy1:wy2, wx1:wx2]), to_lab(out[wy1:wy2, wx1:wx2])
    de = cv2.GaussianBlur(np.linalg.norm(lb - la, axis=2), (0, 0), 1.2)
    dl = cv2.GaussianBlur(lb[..., 0] - la[..., 0], (0, 0), 1.5)
    dc = cv2.GaussianBlur(np.hypot(lb[..., 1] - la[..., 1], lb[..., 2] - la[..., 2]), (0, 0), 1.5)
    soft_shadow = (dl < -3) & (dc < 8) & (dl > -24)
    m = mask[wy1:wy2, wx1:wx2]
    allow = np.ones_like(m) if forbid is None else ~forbid[wy1:wy2, wx1:wx2]
    # 1. strongly changed pixels connected to the mask: legs, poles, a dark base
    strong = (de > 14) & ~soft_shadow & allow
    lab, _ = ndi.label(strong | m, structure=np.ones((3, 3), dtype=bool))
    seeds = np.unique(lab[_dilate(m, 0.003 * d) & (lab > 0)])
    grown = np.isin(lab, seeds[seeds > 0]) | m
    # 2. a thin rim of milder change around that (a white pot rim against a white wall)
    grown |= _dilate(grown, 0.01 * d) & (de > 8) & ~soft_shadow & allow
    # 3. solid, no holes
    grown = ndi.binary_fill_holes(grown)
    res = mask.copy()
    res[wy1:wy2, wx1:wx2] |= grown
    return res


def pull_push(val: np.ndarray, wgt: np.ndarray) -> np.ndarray:
    """Fill a sparsely known field (val where wgt > 0) smoothly everywhere: pyramid pull-push."""
    vals, ws = [val * wgt[..., None]], [wgt.astype(np.float32)]
    while min(ws[-1].shape[:2]) > 8:
        vals.append(cv2.pyrDown(vals[-1]))
        ws.append(cv2.pyrDown(ws[-1]))
    tot = float(ws[-1].sum())
    glob = vals[-1].reshape(-1, val.shape[2]).sum(0) / max(tot, 1e-6)
    cur = np.where(ws[-1][..., None] > 1e-6, vals[-1] / np.maximum(ws[-1][..., None], 1e-6), glob)
    for lvl in range(len(vals) - 2, -1, -1):
        hh, ww = ws[lvl].shape[:2]
        up = cv2.pyrUp(cur, dstsize=(ww, hh))
        conf = np.clip(ws[lvl] * 6.0, 0, 1)[..., None]
        here = vals[lvl] / np.maximum(ws[lvl][..., None], 1e-6)
        cur = here * conf + up * (1 - conf)
    return cur.astype(np.float32)


def membrane_field(orig: np.ndarray, out: np.ndarray, mask: np.ndarray, max_diff: float = 40.0) -> np.ndarray:
    """Seam correction for pasting `out` into `orig` through `mask`: the colour difference measured
    on a thin ring just outside the mask, smoothed, then spread into the mask (membrane cloning).
    Ring pixels where the two images really differ (a shadow running past the mask) are ignored."""
    h, w = mask.shape
    d = diag(h, w)
    o, u = orig.astype(np.float32), out.astype(np.float32)
    outside = (~mask).astype(np.float32)
    sig = max(2.0, 0.0025 * d)
    diff = cv2.GaussianBlur((o - u) * outside[..., None], (0, 0), sig) / np.maximum(
        cv2.GaussianBlur(outside, (0, 0), sig), 1e-3)[..., None]
    ring = _dilate(mask, 0.006 * d) & ~mask
    ok = ring & (np.linalg.norm(diff, axis=2) < max_diff)
    field = pull_push(diff, ok.astype(np.float32))
    return np.clip(field, -max_diff, max_diff) * mask.astype(np.float32)[..., None]


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def shade_layer(orig: np.ndarray, out: np.ndarray, new_masks: list[np.ndarray], skip: np.ndarray | None = None,
                reach: float = 0.06, min_dark: float = 0.04, max_dark: float = 0.6) -> np.ndarray:
    """Contact shadows of the new products as a smooth multiplier for the ORIGINAL pixels.

    The generator's shadow is measured as a low-frequency luminance ratio (output / original) on
    shadow-like pixels next to each new product, then applied to the original photo. The real wall
    or floor texture under a shadow is kept, and the shadow fades out with distance instead of
    ending at a mask edge. Pixels with no measurable darkening get exactly 1.0, so they stay
    identical to the original."""
    h, w = orig.shape[:2]
    d = diag(h, w)
    new = np.zeros((h, w), dtype=bool)
    for mm in new_masks:
        new |= mm
    if not new.any():
        return np.ones((h, w), dtype=np.float32)
    s = 640 / max(h, w)  # work at low resolution: shadows are low-frequency
    sw, sh = max(1, round(w * s)), max(1, round(h * s))
    a = cv2.resize(orig, (sw, sh), interpolation=cv2.INTER_AREA)
    b = cv2.resize(out, (sw, sh), interpolation=cv2.INTER_AREA)
    n_s = cv2.resize(new.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST) > 0
    la, lb = to_lab(a), to_lab(b)
    dc = np.hypot(lb[..., 1] - la[..., 1], lb[..., 2] - la[..., 2])
    ratio = (lb[..., 0] + 4.0) / (la[..., 0] + 4.0)
    ds = diag(sh, sw)
    zone = _dilate(n_s, reach * ds) & ~n_s
    valid = zone & (dc < 10) & (ratio < 1.15)
    if skip is not None:
        valid &= ~(cv2.resize(skip.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST) > 0)
    sig = max(1.5, 0.012 * ds)
    wv = valid.astype(np.float32)
    num = cv2.GaussianBlur(ratio.astype(np.float32) * wv, (0, 0), sig)
    den = cv2.GaussianBlur(wv, (0, 0), sig)
    r_s = np.where(den > 0.05, num / np.maximum(den, 1e-3), 1.0)
    dark = np.clip(1.0 - r_s, 0.0, max_dark)
    dark = dark * _smoothstep(min_dark, 2 * min_dark, dark)
    dist = cv2.distanceTransform((~n_s).astype(np.uint8), cv2.DIST_L2, 5)
    fall = np.clip(1.0 - dist / (reach * ds), 0.0, 1.0) ** 1.5
    shade_s = 1.0 - dark * fall
    shade = cv2.resize(shade_s.astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    shade = np.clip(shade, 1.0 - max_dark, 1.0)
    shade[shade > 0.997] = 1.0
    return shade


def apply_shade(orig: np.ndarray, shade: np.ndarray) -> np.ndarray:
    out = orig.copy()
    m = shade < 1.0
    if m.any():
        out[m] = np.clip(orig[m].astype(np.float32) * shade[m][:, None] + 0.5, 0, 255).astype(np.uint8)
    return out


def paste_seamless(orig: np.ndarray, out: np.ndarray, mask: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    grown = _dilate(mask, 0.006 * diag(*mask.shape))  # the feather reaches a little past the mask
    fixed = np.clip(out.astype(np.float32) + membrane_field(orig, out, grown), 0, 255).astype(np.uint8)
    return paste(orig, fixed, alpha)


def feather(mask: np.ndarray, sigma_frac: float = 0.004) -> np.ndarray:
    h, w = mask.shape
    sigma = max(1.5, sigma_frac * diag(h, w))
    core = cv2.erode(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
    alpha = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), sigma)
    return np.clip(np.maximum(alpha, core.astype(np.float32)), 0, 1)


def paste(orig: np.ndarray, out: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    a = alpha[..., None]
    return np.clip(out.astype(np.float32) * a + orig.astype(np.float32) * (1 - a), 0, 255).astype(np.uint8)


def heatmap(de: np.ndarray, vmax: float = 30.0) -> np.ndarray:
    v = np.clip(de / vmax, 0, 1)
    colored = cv2.applyColorMap((v * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    return cv2.cvtColor(colored, cv2.COLOR_BGR2RGB)


def mask_overlay(base: np.ndarray, mask: np.ndarray, color=(0, 220, 255)) -> np.ndarray:
    out = base.astype(np.float32).copy()
    c = np.array(color, dtype=np.float32)
    out[mask] = out[mask] * 0.55 + c * 0.45
    edge = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    out[edge] = c
    return out.astype(np.uint8)
