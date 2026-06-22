"""COCO dataset loader with RLE mask cropping, overlay rendering, and barcode management."""

import json
import os
import tempfile
from collections import OrderedDict, defaultdict

import cv2
import numpy as np
from pycocotools import mask as mask_util


# ---------------------------------------------------------------------------
# Color utilities (ported from annotation_viewer.py)
# ---------------------------------------------------------------------------

CATEGORIES_LOGISTICS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "categories_logistics.json"
)


def hex_to_bgr(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


def generate_palette(n):
    colors = []
    for i in range(n):
        hue = int(180 * i / n)
        hsv = np.uint8([[[hue, 200, 220]]])
        bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
        colors.append(tuple(int(c) for c in bgr))
    return colors


def vary_color_bgr(base_bgr, variation_index):
    if variation_index == 0:
        return base_bgr
    pixel = np.uint8([[list(base_bgr)]])
    hsv = cv2.cvtColor(pixel, cv2.COLOR_BGR2HSV)[0][0].astype(np.int32)
    hsv[0] = (hsv[0] + variation_index * 25) % 180
    hsv[1] = np.clip(hsv[1] + ((variation_index % 3) - 1) * 30, 40, 255)
    hsv[2] = np.clip(hsv[2] + ((variation_index % 2) * 20 - 10), 80, 255)
    result = cv2.cvtColor(np.uint8([[hsv.astype(np.uint8)]]), cv2.COLOR_HSV2BGR)[0][0]
    return tuple(int(c) for c in result)


def load_color_map(coco_categories):
    color_map = {}
    logistics_colors = {}
    if os.path.exists(CATEGORIES_LOGISTICS_PATH):
        with open(CATEGORIES_LOGISTICS_PATH, "r", encoding="utf-8") as f:
            for entry in json.load(f):
                logistics_colors[entry["name"].lower()] = hex_to_bgr(entry["color"])
    unmatched = []
    for cat in coco_categories:
        name_lower = cat["name"].lower()
        if name_lower in logistics_colors:
            color_map[cat["id"]] = logistics_colors[name_lower]
        else:
            unmatched.append(cat)
    if unmatched:
        palette = generate_palette(len(unmatched) + 1)
        for i, cat in enumerate(unmatched):
            color_map[cat["id"]] = palette[i]
    return color_map


PROXIMITY_THRESHOLD = 100


def bbox_distance(b1, b2):
    x1, y1, w1, h1 = b1
    x2, y2, w2, h2 = b2
    gap_x = max(0, x2 - (x1 + w1), x1 - (x2 + w2))
    gap_y = max(0, y2 - (y1 + h1), y1 - (y2 + h2))
    return gap_x + gap_y


def assign_instance_colors(annotations, base_color_map, roi_cat_id):
    result = {}
    groups = defaultdict(list)
    for ann in annotations:
        groups[ann["category_id"]].append(ann)
    for cat_id, anns in groups.items():
        base = base_color_map.get(cat_id, (200, 200, 200))
        if cat_id == roi_cat_id or len(anns) == 1:
            for ann in anns:
                result[ann["id"]] = base
            continue
        n = len(anns)
        adjacent = defaultdict(set)
        for i in range(n):
            for j in range(i + 1, n):
                if bbox_distance(anns[i]["bbox"], anns[j]["bbox"]) < PROXIMITY_THRESHOLD:
                    adjacent[i].add(j)
                    adjacent[j].add(i)
        var_indices = [0] * n
        for i in range(n):
            used = {var_indices[j] for j in adjacent[i] if j < i}
            idx = 0
            while idx in used:
                idx += 1
            var_indices[i] = idx
        for i, ann in enumerate(anns):
            result[ann["id"]] = vary_color_bgr(base, var_indices[i])
    return result


# ---------------------------------------------------------------------------
# Mask decoding
# ---------------------------------------------------------------------------

def decode_mask(seg, h, w):
    if isinstance(seg, dict):
        if isinstance(seg.get("counts"), list):
            rle = mask_util.frPyObjects(seg, h, w)
        else:
            rle = seg
        return mask_util.decode(rle).astype(np.uint8)
    elif isinstance(seg, list):
        rles = mask_util.frPyObjects(seg, h, w)
        rle = mask_util.merge(rles) if isinstance(rles, list) else rles
        return mask_util.decode(rle).astype(np.uint8)
    return None


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def draw_dashed_rect(img, pt1, pt2, color, thickness=3, dash_length=20, gap_length=12):
    x1, y1 = pt1
    x2, y2 = pt2
    edges = [
        ((x1, y1), (x2, y1)),
        ((x2, y1), (x2, y2)),
        ((x2, y2), (x1, y2)),
        ((x1, y2), (x1, y1)),
    ]
    for (sx, sy), (ex, ey) in edges:
        length = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
        if length == 0:
            continue
        dx, dy = (ex - sx) / length, (ey - sy) / length
        pos = 0
        while pos < length:
            seg_end = min(pos + dash_length, length)
            p1 = (int(sx + dx * pos), int(sy + dy * pos))
            p2 = (int(sx + dx * seg_end), int(sy + dy * seg_end))
            cv2.line(img, p1, p2, color, thickness)
            pos = seg_end + gap_length


def _draw_label_with_bg(overlay, text, x, y, font_scale, color, thickness=1):
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
    cv2.rectangle(overlay, (x - 1, y - th - 2), (x + tw + 1, y + baseline + 1),
                  (0, 0, 0, 180), -1)
    cv2.putText(overlay, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, thickness,
                cv2.LINE_AA)


# ---------------------------------------------------------------------------
# Image cache
# ---------------------------------------------------------------------------

class ImageCache:
    def __init__(self, maxsize=5):
        self._cache = OrderedDict()
        self._maxsize = maxsize

    def load(self, path):
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]
        img_data = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
        self._cache[path] = image
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)
        return image


class CocoDataset:
    def __init__(self):
        self.folder = None
        self.json_path = None
        self.images = {}        # id -> {file_name, width, height}
        self.annotations = {}   # id -> full annotation dict
        self.categories = {}    # id -> name
        self.cat_name_to_id = {}
        self.by_category = {}   # category_name -> [ann_id, ...]
        self.by_barcode = {}    # barcode_value -> [ann_id, ...]
        self.image_frame_index = {}  # image_id -> 0-based frame index
        self._crop_cache = OrderedDict()
        self._raw_data = None
        self._dirty = False
        self.MAX_CACHE = 500

    @staticmethod
    def find_annotation_json(folder_path):
        """Find the first COCO JSON file in the annotation subfolder."""
        ann_dir = os.path.join(folder_path, "annotation")
        if not os.path.isdir(ann_dir):
            return None
        for f in sorted(os.listdir(ann_dir)):
            if f.lower().endswith(".json") and not f.endswith(".bak"):
                return os.path.join(ann_dir, f)
        return None

    def load(self, folder_path):
        self.folder = folder_path
        self.json_path = self.find_annotation_json(folder_path)
        if not self.json_path:
            raise FileNotFoundError(f"No annotation JSON found in: {os.path.join(folder_path, 'annotation')}")

        with open(self.json_path, "r", encoding="utf-8") as f:
            self._raw_data = json.load(f)

        # Parse categories
        self.categories = {}
        self.cat_name_to_id = {}
        for cat in self._raw_data.get("categories", []):
            self.categories[cat["id"]] = cat["name"]
            self.cat_name_to_id[cat["name"]] = cat["id"]

        # Parse images
        self.images = {}
        self.image_frame_index = {}
        for idx, img in enumerate(self._raw_data.get("images", [])):
            self.images[img["id"]] = {
                "file_name": img["file_name"],
                "width": img["width"],
                "height": img["height"],
            }
            self.image_frame_index[img["id"]] = idx

        # Parse annotations and build indices
        self.annotations = {}
        self.by_category = {}
        self.by_barcode = {}

        for ann in self._raw_data.get("annotations", []):
            ann_id = ann["id"]
            self.annotations[ann_id] = ann

            cat_name = self.categories.get(ann["category_id"], "unknown")

            if cat_name not in self.by_category:
                self.by_category[cat_name] = []
            self.by_category[cat_name].append(ann_id)

            # Index items by barcode
            if cat_name == "item":
                barcode = ann.get("attributes", {}).get("barcode", "") or ""
                key = barcode if barcode else "__no_barcode__"
                if key not in self.by_barcode:
                    self.by_barcode[key] = []
                self.by_barcode[key].append(ann_id)

        self._crop_cache.clear()
        self._dirty = False

    def get_group_structure(self):
        """Return the grouping structure for frontend rendering."""
        result = {}
        for cat_name, ann_ids in self.by_category.items():
            if cat_name == "roi":
                continue

            if cat_name == "item":
                groups = {}
                for barcode_key, b_ann_ids in sorted(self.by_barcode.items(),
                                                      key=lambda x: (-len(x[1]), x[0])):
                    display_key = barcode_key if barcode_key != "__no_barcode__" else "(바코드 없음)"
                    items = []
                    for aid in b_ann_ids:
                        ann = self.annotations[aid]
                        img = self.images.get(ann["image_id"], {})
                        attrs = ann.get("attributes", {})
                        items.append({
                            "ann_id": aid,
                            "barcode": attrs.get("barcode", ""),
                            "shipment_id": attrs.get("shipment_id", ""),
                            "image_file": img.get("file_name", ""),
                            "category": cat_name,
                        })
                    groups[display_key] = items
                result[cat_name] = {"group_by": "barcode", "groups": groups, "total": len(ann_ids)}
            else:
                items = []
                for aid in ann_ids:
                    ann = self.annotations[aid]
                    img = self.images.get(ann["image_id"], {})
                    items.append({
                        "ann_id": aid,
                        "barcode": "",
                        "shipment_id": "",
                        "image_file": img.get("file_name", ""),
                        "category": cat_name,
                    })
                result[cat_name] = {"group_by": "all", "groups": {"전체": items}, "total": len(ann_ids)}

        return result

    def get_crop_jpeg(self, ann_id):
        """Generate a cropped JPEG of the masked object."""
        if ann_id in self._crop_cache:
            self._crop_cache.move_to_end(ann_id)
            return self._crop_cache[ann_id]

        ann = self.annotations.get(ann_id)
        if ann is None:
            return None

        img_info = self.images.get(ann["image_id"])
        if img_info is None:
            return None

        img_path = os.path.join(self.folder, img_info["file_name"])
        if not os.path.exists(img_path):
            return None

        # Load image (Unicode path safe)
        img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None

        # Decode RLE mask
        seg = ann.get("segmentation", {})
        if isinstance(seg, dict) and "counts" in seg:
            if isinstance(seg["counts"], str):
                # Compressed RLE
                rle = seg
            else:
                # Uncompressed RLE
                rle = mask_util.frPyObjects(seg, seg["size"][0], seg["size"][1])
            mask = mask_util.decode(rle)
        elif isinstance(seg, list):
            # Polygon format
            h, w = img.shape[:2]
            rle = mask_util.frPyObjects(seg, h, w)
            mask = mask_util.decode(mask_util.merge(rle))
        else:
            return None

        if mask.ndim == 3:
            mask = mask[:, :, 0]

        # Find bounding box from mask
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if not rows.any() or not cols.any():
            return None

        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]

        # Add padding
        padding = 5
        h, w = mask.shape
        rmin = max(0, rmin - padding)
        rmax = min(h - 1, rmax + padding)
        cmin = max(0, cmin - padding)
        cmax = min(w - 1, cmax + padding)

        # Crop and apply mask
        crop = img[rmin:rmax + 1, cmin:cmax + 1].copy()
        mask_crop = mask[rmin:rmax + 1, cmin:cmax + 1]
        crop[mask_crop == 0] = 0

        # Encode as JPEG
        _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        jpeg_bytes = buf.tobytes()

        # Cache with LRU eviction
        if len(self._crop_cache) >= self.MAX_CACHE:
            self._crop_cache.popitem(last=False)
        self._crop_cache[ann_id] = jpeg_bytes

        return jpeg_bytes

    # Default attributes per category
    DEFAULT_ATTRS = {
        "item": {"barcode": "", "shipment_id": ""},
        "roi": {},
    }
    # All other categories default to {"iscrowd": "0"}

    def _get_default_attrs(self, cat_name):
        return dict(self.DEFAULT_ATTRS.get(cat_name, {"iscrowd": "0"}))

    def update_category(self, ann_id, new_category_name):
        """Update category for an annotation, replacing attributes to match target format."""
        ann = self.annotations.get(ann_id)
        if ann is None:
            return False

        new_cat_id = self.cat_name_to_id.get(new_category_name)
        if new_cat_id is None:
            return False

        old_cat_id = ann["category_id"]
        old_cat_name = self.categories.get(old_cat_id, "unknown")

        if old_cat_name == new_category_name:
            return True  # no change

        # Update by_category index
        if old_cat_name in self.by_category and ann_id in self.by_category[old_cat_name]:
            self.by_category[old_cat_name].remove(ann_id)
            if not self.by_category[old_cat_name]:
                del self.by_category[old_cat_name]

        if new_category_name not in self.by_category:
            self.by_category[new_category_name] = []
        self.by_category[new_category_name].append(ann_id)

        # Update by_barcode index
        if old_cat_name == "item":
            barcode = ann.get("attributes", {}).get("barcode", "") or ""
            old_key = barcode if barcode else "__no_barcode__"
            if old_key in self.by_barcode and ann_id in self.by_barcode[old_key]:
                self.by_barcode[old_key].remove(ann_id)
                if not self.by_barcode[old_key]:
                    del self.by_barcode[old_key]

        if new_category_name == "item":
            if "__no_barcode__" not in self.by_barcode:
                self.by_barcode["__no_barcode__"] = []
            self.by_barcode["__no_barcode__"].append(ann_id)

        # Update category_id and replace attributes
        ann["category_id"] = new_cat_id
        ann["attributes"] = self._get_default_attrs(new_category_name)

        # Update raw data
        for raw_ann in self._raw_data.get("annotations", []):
            if raw_ann["id"] == ann_id:
                raw_ann["category_id"] = new_cat_id
                raw_ann["attributes"] = self._get_default_attrs(new_category_name)
                break

        self._dirty = True
        return True

    def update_barcode(self, ann_id, new_barcode):
        """Update barcode for an annotation and re-index."""
        ann = self.annotations.get(ann_id)
        if ann is None:
            return False

        cat_name = self.categories.get(ann["category_id"], "")
        if cat_name != "item":
            return False

        old_barcode = ann.get("attributes", {}).get("barcode", "") or ""
        old_key = old_barcode if old_barcode else "__no_barcode__"
        new_key = new_barcode if new_barcode else "__no_barcode__"

        # Update annotation
        if "attributes" not in ann:
            ann["attributes"] = {}
        ann["attributes"]["barcode"] = new_barcode

        # Re-index
        if old_key in self.by_barcode and ann_id in self.by_barcode[old_key]:
            self.by_barcode[old_key].remove(ann_id)
            if not self.by_barcode[old_key]:
                del self.by_barcode[old_key]

        if new_key not in self.by_barcode:
            self.by_barcode[new_key] = []
        self.by_barcode[new_key].append(ann_id)

        # Update raw data
        for raw_ann in self._raw_data.get("annotations", []):
            if raw_ann["id"] == ann_id:
                if "attributes" not in raw_ann:
                    raw_ann["attributes"] = {}
                raw_ann["attributes"]["barcode"] = new_barcode
                break

        self._dirty = True
        return True

    def save_annotations(self):
        """Save modified annotations to disk (temp-rename pattern)."""
        if not self._dirty or not self.json_path:
            return False

        # Create .bak backup on first save
        bak_path = self.json_path + ".bak"
        if not os.path.exists(bak_path):
            import shutil
            shutil.copy2(self.json_path, bak_path)

        dir_path = os.path.dirname(self.json_path)
        fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=dir_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._raw_data, f, ensure_ascii=False)
            os.replace(tmp_path, self.json_path)
            self._dirty = False
            return True
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def get_annotation_info(self, ann_id):
        """Get metadata for a single annotation."""
        ann = self.annotations.get(ann_id)
        if ann is None:
            return None
        img = self.images.get(ann["image_id"], {})
        attrs = ann.get("attributes", {})
        cat_name = self.categories.get(ann["category_id"], "unknown")
        return {
            "ann_id": ann_id,
            "barcode": attrs.get("barcode", ""),
            "shipment_id": attrs.get("shipment_id", ""),
            "image_file": img.get("file_name", ""),
            "category": cat_name,
            "frame": self.image_frame_index.get(ann["image_id"], -1),
        }

    # ------------------------------------------------------------------
    # Image review mode methods
    # ------------------------------------------------------------------

    def get_image_list(self):
        """Return sorted list of images with annotation counts."""
        # Build ann count index
        ann_counts = defaultdict(int)
        for a in self.annotations.values():
            ann_counts[a["image_id"]] += 1

        result = []
        for img_id, img_info in self.images.items():
            result.append({
                "image_id": img_id,
                "file_name": img_info["file_name"],
                "width": img_info["width"],
                "height": img_info["height"],
                "ann_count": ann_counts.get(img_id, 0),
                "frame": self.image_frame_index.get(img_id, 0),
            })
        result.sort(key=lambda x: x["frame"])
        return result

    def get_annotations_for_image(self, image_id):
        """Return all annotations for an image with resolved category names."""
        anns = [a for a in self.annotations.values() if a["image_id"] == image_id]
        result = []
        for ann in anns:
            attrs = ann.get("attributes", {})
            result.append({
                "ann_id": ann["id"],
                "category_id": ann["category_id"],
                "category": self.categories.get(ann["category_id"], "unknown"),
                "bbox": ann.get("bbox", []),
                "barcode": attrs.get("barcode", ""),
                "shipment_id": attrs.get("shipment_id", ""),
                "attributes": attrs,
            })
        return result

    def _get_roi_cat_id(self):
        """Find the category ID for 'roi'."""
        for cid, name in self.categories.items():
            if name.lower() == "roi":
                return cid
        return None

    def _init_color_maps(self):
        """Initialize color maps from raw category data if not already done."""
        if not hasattr(self, "_base_color_map") or self._base_color_map is None:
            cats = self._raw_data.get("categories", [])
            self._base_color_map = load_color_map(cats)
            self._all_categories = cats

    # Maximum pixel count for rendering (downscale if larger)
    RENDER_MAX_PIXELS = 1920 * 1080

    def _get_render_scale(self, img_w, img_h):
        """Compute scale factor to keep rendering within RENDER_MAX_PIXELS."""
        pixels = img_w * img_h
        if pixels <= self.RENDER_MAX_PIXELS:
            return 1.0
        return (self.RENDER_MAX_PIXELS / pixels) ** 0.5

    def render_overlay_jpeg(self, image_id, show_roi=True, error_ann_ids=None,
                            quality=85):
        """Render overlay visualization and return JPEG bytes.

        Optimized: downscale large images before rendering, batch mask blending,
        use bbox center instead of cv2.moments, cache rendered overlays.
        """
        if error_ann_ids is None:
            error_ann_ids = set()

        # Check overlay cache
        cache_key = (image_id, show_roi, frozenset(error_ann_ids))
        if not hasattr(self, "_overlay_cache"):
            self._overlay_cache = OrderedDict()
        if cache_key in self._overlay_cache:
            self._overlay_cache.move_to_end(cache_key)
            return self._overlay_cache[cache_key]

        img_info = self.images.get(image_id)
        if img_info is None:
            return None

        img_path = os.path.join(self.folder, img_info["file_name"])
        if not os.path.exists(img_path):
            return None

        if not hasattr(self, "_img_cache"):
            self._img_cache = ImageCache(maxsize=5)

        image_full = self._img_cache.load(img_path)
        if image_full is None:
            return None

        self._init_color_maps()
        roi_cat_id = self._get_roi_cat_id()

        anns = [a for a in self.annotations.values() if a["image_id"] == image_id]
        orig_h, orig_w = img_info["height"], img_info["width"]

        # Downscale for rendering performance
        scale = self._get_render_scale(orig_w, orig_h)
        if scale < 1.0:
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)
            image = cv2.resize(image_full, (new_w, new_h), interpolation=cv2.INTER_AREA)
        else:
            scale = 1.0
            new_w, new_h = orig_w, orig_h
            image = image_full

        overlay = image.copy()
        alpha = 0.4

        # Build a single color overlay image and combined mask for batch blending
        color_layer = np.zeros_like(image)
        combined_mask = np.zeros((new_h, new_w), dtype=np.uint8)

        # Per-annotation data for contours/labels (collected during mask decode)
        ann_render_data = []

        for ann in anns:
            cat_id = ann["category_id"]
            if cat_id == roi_cat_id:
                continue

            seg = ann.get("segmentation")
            if seg is None:
                continue

            # Decode mask at original resolution, then downscale
            mask_full = decode_mask(seg, orig_h, orig_w)
            if mask_full is None:
                continue

            if scale < 1.0:
                mask = cv2.resize(mask_full, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
            else:
                mask = mask_full

            color = assign_instance_colors([ann], self._base_color_map, roi_cat_id).get(
                ann["id"], (200, 200, 200))

            # Paint color into the color layer where mask is active
            mask_bool = mask > 0
            color_layer[mask_bool] = color
            combined_mask[mask_bool] = 255

            # Store contour + label data for later drawing
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            # Use bbox center (fast) instead of cv2.moments
            bx, by, bw, bh = ann.get("bbox", [0, 0, 0, 0])
            cx = int((bx + bw / 2) * scale)
            cy = int((by + bh / 2) * scale)

            ann_render_data.append({
                "ann": ann,
                "color": color,
                "contours": contours,
                "cx": cx, "cy": cy,
            })

        # Single batch alpha-blend using cv2.addWeighted on masked region
        if combined_mask.any():
            mask3 = cv2.merge([combined_mask, combined_mask, combined_mask])
            # Blend: overlay = image * (1-alpha) + color_layer * alpha, only where mask
            blended = cv2.addWeighted(image, 1 - alpha, color_layer, alpha, 0)
            np.copyto(overlay, blended, where=(mask3 > 0))

        # Draw contours and labels (lightweight operations)
        for rd in ann_render_data:
            ann = rd["ann"]
            color = rd["color"]
            ann_id = ann["id"]

            cv2.drawContours(overlay, rd["contours"], -1, color, 1)

            # Error highlight
            if ann_id in error_ann_ids:
                cv2.drawContours(overlay, rd["contours"], -1, (0, 0, 255), 2)
                bx, by, bw, bh = [int(v * scale) for v in ann["bbox"]]
                cv2.putText(overlay, "!", (bx + bw - 15, by + 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

            # Label
            cx, cy = rd["cx"], rd["cy"]
            cat_id = ann["category_id"]
            cat_name = self.categories.get(cat_id, str(cat_id))
            attrs = ann.get("attributes", {})
            barcode = attrs.get("barcode", "")
            shipment = attrs.get("shipment_id", "")

            if barcode or shipment:
                line1 = f"{cat_name} | {barcode}" if barcode else cat_name
                _draw_label_with_bg(overlay, line1, cx - 50, cy - 8, 0.35, color, 1)
                if shipment:
                    _draw_label_with_bg(overlay, f"ship: {shipment}", cx - 50, cy + 8, 0.3, color, 1)
            else:
                _draw_label_with_bg(overlay, cat_name, cx - 20, cy, 0.35, color, 1)

        # ROI (dashed rectangle)
        if show_roi and roi_cat_id is not None:
            for ann in anns:
                if ann["category_id"] != roi_cat_id:
                    continue
                roi_color = self._base_color_map.get(roi_cat_id, (45, 45, 225))
                x, y, w, h = [int(v * scale) for v in ann["bbox"]]
                draw_dashed_rect(overlay, (x, y), (x + w, y + h), roi_color, 1)
                _draw_label_with_bg(overlay, "ROI", x + 4, y + 16, 0.4, roi_color, 1)

        # Legend
        if self._all_categories and self._base_color_map:
            panel_w = 200
            panel_h = 18 * len(self._all_categories) + 12
            h_img, w_img = overlay.shape[:2]
            px = w_img - panel_w - 8
            py = 8
            if py + panel_h <= h_img and px > 0:
                sub = overlay[py:py + panel_h, px:px + panel_w].copy()
                cv2.rectangle(overlay, (px, py), (px + panel_w, py + panel_h), (0, 0, 0), -1)
                cv2.addWeighted(sub, 0.3, overlay[py:py + panel_h, px:px + panel_w], 0.7, 0,
                                overlay[py:py + panel_h, px:px + panel_w])
                for i, cat in enumerate(self._all_categories):
                    ly = py + 14 + i * 18
                    clr = self._base_color_map.get(cat["id"], (200, 200, 200))
                    cv2.rectangle(overlay, (px + 6, ly - 7), (px + 16, ly + 3), clr, -1)
                    suffix = " (dashed)" if cat.get("name", "").lower() == "roi" else ""
                    cv2.putText(overlay, cat["name"] + suffix, (px + 22, ly),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (255, 255, 255), 1,
                                cv2.LINE_AA)

        # HUD
        frame_idx = self.image_frame_index.get(image_id, 0)
        total = len(self.images)
        hud = f"[{frame_idx + 1}/{total}] {img_info['file_name']}  ({len(anns)} ann)"
        if error_ann_ids:
            hud += f"  | {len(error_ann_ids)} errors"
        (tw, th), _ = cv2.getTextSize(hud, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
        cv2.rectangle(overlay, (4, 4), (8 + tw, 8 + th + 4), (0, 0, 0), -1)
        cv2.putText(overlay, hud, (6, 6 + th + 1),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

        _, buf = cv2.imencode(".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, quality])
        result = buf.tobytes()

        # Cache overlay (LRU, max 20)
        if len(self._overlay_cache) >= 20:
            self._overlay_cache.popitem(last=False)
        self._overlay_cache[cache_key] = result

        return result

    def invalidate_overlay_cache(self, image_id=None):
        """Clear overlay cache. If image_id given, only clear entries for that image."""
        if not hasattr(self, "_overlay_cache"):
            return
        if image_id is None:
            self._overlay_cache.clear()
        else:
            keys_to_del = [k for k in self._overlay_cache if k[0] == image_id]
            for k in keys_to_del:
                del self._overlay_cache[k]

    def prefetch_overlay(self, image_id, show_roi=True, error_ann_ids=None):
        """Pre-render overlay in background thread."""
        import threading
        def _render():
            self.render_overlay_jpeg(image_id, show_roi, error_ann_ids)
        threading.Thread(target=_render, daemon=True).start()

    def hit_test(self, image_id, x, y):
        """Find annotation at pixel coordinates (x, y). Returns annotation info or None."""
        img_info = self.images.get(image_id)
        if img_info is None:
            return None

        roi_cat_id = self._get_roi_cat_id()
        anns = [a for a in self.annotations.values() if a["image_id"] == image_id]
        img_h, img_w = img_info["height"], img_info["width"]
        ix, iy = int(round(x)), int(round(y))

        hits = []
        for ann in anns:
            if ann["category_id"] == roi_cat_id:
                continue
            # Try mask first
            seg = ann.get("segmentation")
            if seg is not None:
                mask = decode_mask(seg, img_h, img_w)
                if mask is not None and 0 <= iy < mask.shape[0] and 0 <= ix < mask.shape[1]:
                    if mask[iy, ix] > 0:
                        hits.append(ann)
                        continue
            # Fallback to bbox
            bx, by, bw, bh = ann.get("bbox", [0, 0, 0, 0])
            if bx <= ix <= bx + bw and by <= iy <= by + bh:
                hits.append(ann)

        if not hits:
            return None

        # Return smallest (most specific) annotation
        hits.sort(key=lambda a: a.get("area", float("inf")))
        ann = hits[0]
        attrs = ann.get("attributes", {})
        return {
            "ann_id": ann["id"],
            "image_id": image_id,
            "category_id": ann["category_id"],
            "category": self.categories.get(ann["category_id"], "unknown"),
            "bbox": ann.get("bbox", []),
            "barcode": attrs.get("barcode", ""),
            "shipment_id": attrs.get("shipment_id", ""),
            "attributes": attrs,
        }
