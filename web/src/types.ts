export type Box = [number, number, number, number];

export type StageId = "scan" | "plan" | "search" | "pick" | "render" | "verify" | "fix" | "result";

export interface RunEvent {
  t: number;
  stage: StageId | "error";
  title: string;
  detail?: string;
  model?: string;
  sec?: number;
  usd?: number;
  status?: "start" | "done";
  scores?: number[];
}

export type ObjectClass = "keep" | "replace" | "remove";

export interface SceneObject {
  id: string;
  name: string;
  category: string;
  class: ObjectClass;
  reason: string;
  sam_phrase: string;
  spark_box: Box;
  box: Box;
  outline: string;
  iou: number | null;
  conf: number | null;
  area_frac: number | null;
}

export interface FixedElement {
  name: string;
  box: Box;
}

export interface SizeCm {
  w: number;
  d: number;
  h: number;
}

export interface Funnel {
  kind: number;
  fits: number;
  size_tolerance: number;
  clean: number;
  shortlist?: number;
  ranked_by?: string[];
}

export interface Slot {
  id: string;
  kind: string;
  action: "add" | "replace";
  replaces: string;
  target_px: Box;
  size_cm: SizeCm;
  look: string;
  color_family: string;
  placement: string;
  why: string;
  funnel?: Funnel;
  shortlist?: string[];
  sheet?: string;
  pick?: string;
  alternates?: string[];
  reason?: string;
  pick_number?: number;
}

export interface Scan {
  size: [number, number];
  room_type: string;
  summary: string;
  dimensions_cm: { width: number; depth: number; ceiling: number; confidence: string; basis: string };
  light: { direction: string; color_temperature: string; quality: string };
  fixed: FixedElement[];
  objects: SceneObject[];
  plan: { title: string; style: string; palette: string[]; idea: string; slots: Slot[] };
  stats: {
    spark_sec: number;
    sam_sec: number;
    sam_requests: number;
    objects: number;
    outlined: number;
    mean_iou: number | null;
    classes: Record<ObjectClass, number>;
  };
}

export interface Plan {
  title: string;
  idea: string;
  style: string;
  palette: string[];
  set_comment: string;
  slots: Slot[];
}

export interface Product {
  id: string;
  kind: string;
  title: string;
  name: string;
  brand: string;
  size_cm: SizeCm;
  size_in: SizeCm;
  color: string | null;
  materials: string[];
  style: string[];
  description: string;
  render_risk: string;
  url: string;
  thumb: string;
  image: string;
  source: string;
}

export type VerdictKind = "match" | "partial" | "mismatch" | "missing";

export interface Verdict {
  index?: number;
  verdict?: VerdictKind;
  score?: number;
  shape?: string;
  color?: string;
  material_pattern?: string;
  scale_ok?: boolean;
  placement_ok?: boolean;
  issues?: string[];
  fix?: string;
  fixed?: boolean;
  error?: string;
}

export interface SceneVerdict {
  photoreal: number;
  cohesion: number;
  renter_rule_ok: boolean;
  removals_ok: boolean;
  artifacts: string[];
  overall: number;
  comment: string;
}

export interface Candidate {
  index: number;
  score: number;
  judge: { products?: Verdict[]; scene?: SceneVerdict; error?: string };
  raw: string;
  comp: string;
  drift: { dE_mean: number; changed_frac_dE10: number; ssim_static: number };
  align: { ok?: boolean; max_corner_shift_px?: number };
  seg_how: string[];
}

export interface FixRecord {
  /** "clear": clutter the render left behind, cleared with a second Muse Image pass */
  kind?: "clear";
  slot: string;
  product?: string;
  /** clear records: the objects and how much of each SAM 3.1 still found, before and after */
  objects?: string[];
  names?: string[];
  before: Verdict & { left?: Record<string, number> };
  after: Verdict & { left?: Record<string, number> };
  accepted: boolean;
  region: Box;
  images: [string, string];
  /** redraw records: rounds run and every try Spark judged (the best try per round competes) */
  rounds?: number;
  tries?: { round: number; verdict?: Verdict["verdict"]; score?: number }[];
}

export interface ResultProduct {
  slot: string;
  product: string;
  box: Box;
  outline: string;
  crop: string;
  verdict: Verdict;
  fixed: boolean;
}

export interface Proof {
  untouched_pct: number;
  edited_pct: number;
  layers_pct?: { products: number; cleared: number; shadows: number };
  model_drift_dE: number;
  model_drift_changed_pct: number;
  ssim_static: number;
  alignment_shift_px: number;
}

export interface Result {
  after: string;
  edit_mask: string;
  drift: string;
  layers?: string;
  lossless?: { before: string; after: string };
  edit_outline: string;
  products: ResultProduct[];
  removed: { id: string; name: string; box: Box; outline: string }[];
  proof: Proof;
  scene: SceneVerdict;
  /** set when a fix was accepted and Spark judged the finished room again: the verdict on the first render */
  scene_first_render?: SceneVerdict;
  final_check_products?: Verdict[];
}

export interface Run {
  id: string;
  version: number;
  status: "running" | "done" | "error";
  events: RunEvent[];
  room: { w: number; h: number; before: string; title?: string; ai_generated?: boolean; made_with?: string };
  brief: string;
  mode: "refresh" | "furnish";
  elapsed_s?: number;
  cost_usd?: number;
  scan?: Scan;
  plan?: Plan;
  products?: Record<string, Product>;
  render?: { candidates: Candidate[]; chosen: number; prompt: string };
  fixes?: FixRecord[];
  result?: Result;
  error?: string;
}

export interface Demo {
  id: string;
  title: string;
  kicker: string;
  brief: string;
  mode: "refresh" | "furnish";
  before: string;
  after: string;
  products: number;
  untouched_pct: number;
  cost_usd: number;
  elapsed_s: number;
}

export interface AppConfig {
  live: boolean;
  reason?: string;
  runs_left_today?: number;
  catalog_size: number;
  max_upload_mb: number;
}
