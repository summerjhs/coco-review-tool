"""Flask application for Mask Review Tool v2."""

import os
import string
from datetime import datetime
from io import BytesIO

from flask import Flask, jsonify, render_template, request, send_file

from coco_utils import CocoDataset
from export_utils import export_errors_xlsx, export_memos_xlsx

app = Flask(__name__)
dataset = CocoDataset()
sample_dir = ""  # set on load
memos = {}  # ann_id (or negative id for missing) -> memo dict


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/browse")
def browse():
    """List directories for folder browser."""
    path = request.args.get("path", "")

    if not path:
        drives = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:/"
            if os.path.exists(drive):
                drives.append(drive)
        return jsonify({"path": "", "dirs": drives, "has_annotation": False})

    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return jsonify({"error": "Directory not found"}), 404

    dirs = []
    try:
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            if os.path.isdir(full) and not entry.startswith("."):
                dirs.append(entry)
    except PermissionError:
        pass

    has_annotation = CocoDataset.find_annotation_json(path) is not None

    return jsonify({
        "path": path.replace("\\", "/"),
        "dirs": dirs,
        "has_annotation": has_annotation,
    })


@app.route("/api/load", methods=["POST"])
def load_dataset():
    """Load COCO dataset from a folder."""
    global sample_dir
    data = request.get_json()
    folder = data.get("folder", "")
    sample_dir = data.get("sample_dir", "")

    if not folder or not os.path.isdir(folder):
        return jsonify({"error": "Invalid folder path"}), 400

    try:
        dataset.load(folder)
        structure = dataset.get_group_structure()
        return jsonify({"ok": True, "structure": structure})
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sample_dir", methods=["POST"])
def set_sample_dir():
    """Update the sample images directory without reloading the dataset."""
    global sample_dir
    data = request.get_json()
    new_dir = data.get("sample_dir", "")

    if new_dir and not os.path.isdir(new_dir):
        return jsonify({"error": "Directory not found"}), 404

    sample_dir = new_dir
    return jsonify({"ok": True, "sample_dir": sample_dir})


@app.route("/api/structure")
def get_structure():
    """Return current group structure."""
    if not dataset.folder:
        return jsonify({"error": "No dataset loaded"}), 400
    return jsonify(dataset.get_group_structure())


@app.route("/api/crop/<int:ann_id>")
def get_crop(ann_id):
    """Return cropped JPEG for an annotation."""
    if not dataset.folder:
        return "No dataset loaded", 400

    jpeg = dataset.get_crop_jpeg(ann_id)
    if jpeg is None:
        return "Crop not found", 404

    from io import BytesIO
    return send_file(BytesIO(jpeg), mimetype="image/jpeg")


@app.route("/api/categories")
def get_categories():
    """Return list of available categories."""
    if not dataset.folder:
        return jsonify({"error": "No dataset loaded"}), 400
    cats = [{"id": cid, "name": name} for cid, name in dataset.categories.items()]
    cats.sort(key=lambda x: x["name"])
    return jsonify(cats)


@app.route("/api/category/update", methods=["POST"])
def update_category():
    """Update category for an annotation."""
    data = request.get_json()
    ann_id = data.get("ann_id")
    new_category = data.get("new_category", "")

    if ann_id is None or not new_category:
        return jsonify({"error": "ann_id and new_category required"}), 400

    if not dataset.update_category(int(ann_id), new_category):
        return jsonify({"error": "Update failed"}), 400

    dataset.save_annotations()
    return jsonify({"ok": True, "ann_id": ann_id, "new_category": new_category})


@app.route("/api/barcode/update", methods=["POST"])
def update_barcode():
    """Update barcode for an annotation."""
    data = request.get_json()
    ann_id = data.get("ann_id")
    new_barcode = data.get("new_barcode", "")

    if ann_id is None:
        return jsonify({"error": "ann_id required"}), 400

    if not dataset.update_barcode(int(ann_id), new_barcode):
        return jsonify({"error": "Update failed"}), 400

    dataset.save_annotations()
    return jsonify({"ok": True, "ann_id": ann_id, "new_barcode": new_barcode})


@app.route("/api/export", methods=["POST"])
def export():
    """Export error-checked items to XLSX."""
    data = request.get_json()
    ann_ids = data.get("ann_ids", [])

    if not ann_ids:
        return jsonify({"error": "No items to export"}), 400

    ann_ids = [int(x) for x in ann_ids]
    xlsx_buf = export_errors_xlsx(dataset, ann_ids)

    return send_file(
        xlsx_buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="error_items.xlsx",
    )


@app.route("/api/samples/<path:barcode>")
def list_samples(barcode):
    """List sample images for a barcode."""
    if not sample_dir:
        return jsonify({"barcode": barcode, "files": [], "count": 0})

    folder = os.path.join(sample_dir, barcode)
    if not os.path.isdir(folder):
        return jsonify({"barcode": barcode, "files": [], "count": 0})

    files = sorted([
        f for f in os.listdir(folder)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ])
    return jsonify({"barcode": barcode, "files": files, "count": len(files)})


@app.route("/api/samples/<path:barcode>/<path:filename>")
def get_sample(barcode, filename):
    """Serve a sample image file."""
    if not sample_dir:
        return "No sample directory", 404

    filepath = os.path.join(sample_dir, barcode, filename)
    if not os.path.isfile(filepath):
        return "File not found", 404

    return send_file(filepath, mimetype="image/jpeg")


# ======================================================================
# Review mode API routes
# ======================================================================

@app.route("/review")
def review_page():
    return render_template("review.html")


@app.route("/api/images")
def get_images():
    """Return list of images in the loaded dataset."""
    if not dataset.folder:
        return jsonify({"error": "No dataset loaded"}), 400
    return jsonify(dataset.get_image_list())


@app.route("/api/overlay/<int:image_id>")
def get_overlay(image_id):
    """Return rendered overlay JPEG for an image."""
    if not dataset.folder:
        return "No dataset loaded", 400

    show_roi = request.args.get("show_roi", "1") == "1"

    # Collect error ann_ids for this image
    error_ids = set()
    for mid, memo in memos.items():
        if memo.get("image_id") == image_id:
            if memo.get("type") != "missing":
                error_ids.add(mid)

    jpeg = dataset.render_overlay_jpeg(image_id, show_roi=show_roi,
                                       error_ann_ids=error_ids)
    if jpeg is None:
        return "Overlay not available", 404

    from flask import Response
    return Response(jpeg, mimetype="image/jpeg",
                    headers={"Content-Length": len(jpeg),
                             "Cache-Control": "no-cache"})


@app.route("/api/hit_test", methods=["POST"])
def hit_test():
    """Find annotation at given pixel coordinates."""
    if not dataset.folder:
        return jsonify({"error": "No dataset loaded"}), 400

    data = request.get_json()
    image_id = data.get("image_id")
    x = data.get("x", 0)
    y = data.get("y", 0)

    if image_id is None:
        return jsonify({"error": "image_id required"}), 400

    result = dataset.hit_test(int(image_id), float(x), float(y))
    if result is None:
        return jsonify({"hit": False, "x": x, "y": y})

    # Attach existing memo if any
    existing_memo = memos.get(result["ann_id"])
    if existing_memo:
        result["memo"] = existing_memo.get("text", "")
    else:
        result["memo"] = ""

    result["hit"] = True
    return jsonify(result)


@app.route("/api/memo", methods=["POST"])
def save_memo():
    """Save an error memo for an annotation or missing object."""
    data = request.get_json()
    ann_id = data.get("ann_id")
    image_id = data.get("image_id")
    text = data.get("text", "").strip()
    click_x = data.get("x")
    click_y = data.get("y")
    memo_type = data.get("type", "annotation")

    if not text:
        return jsonify({"error": "text required"}), 400
    if image_id is None:
        return jsonify({"error": "image_id required"}), 400

    image_id = int(image_id)
    img_info = dataset.images.get(image_id, {})

    if memo_type == "missing":
        # Generate unique negative ID for missing objects
        memo_id = -int(datetime.now().timestamp() * 1000) % 1000000
        memos[memo_id] = {
            "memo_id": memo_id,
            "image_id": image_id,
            "image_file": img_info.get("file_name", ""),
            "category": "(missing)",
            "ann_id": memo_id,
            "barcode": "",
            "shipment_id": "",
            "text": text,
            "type": "missing",
            "click_xy": (int(click_x or 0), int(click_y or 0)),
            "frame_index": dataset.image_frame_index.get(image_id, 0),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        dataset.invalidate_overlay_cache(image_id)
        return jsonify({"ok": True, "memo_id": memo_id})
    else:
        if ann_id is None:
            return jsonify({"error": "ann_id required"}), 400
        ann_id = int(ann_id)
        ann = dataset.annotations.get(ann_id)
        if ann is None:
            return jsonify({"error": "annotation not found"}), 404

        attrs = ann.get("attributes", {})
        memos[ann_id] = {
            "memo_id": ann_id,
            "image_id": image_id,
            "image_file": img_info.get("file_name", ""),
            "category": dataset.categories.get(ann["category_id"], "unknown"),
            "ann_id": ann_id,
            "barcode": attrs.get("barcode", ""),
            "shipment_id": attrs.get("shipment_id", ""),
            "text": text,
            "type": "annotation",
            "frame_index": dataset.image_frame_index.get(image_id, 0),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        dataset.invalidate_overlay_cache(image_id)
        return jsonify({"ok": True, "memo_id": ann_id})


@app.route("/api/memo/<int:memo_id>", methods=["DELETE"])
def delete_memo(memo_id):
    """Delete a memo."""
    if memo_id in memos:
        image_id = memos[memo_id].get("image_id")
        del memos[memo_id]
        dataset.invalidate_overlay_cache(image_id)
        return jsonify({"ok": True})
    return jsonify({"error": "memo not found"}), 404


@app.route("/api/prefetch/<int:image_id>", methods=["POST"])
def prefetch_overlay(image_id):
    """Pre-render overlay for next/prev image in background."""
    if not dataset.folder:
        return jsonify({"ok": False}), 400
    show_roi = request.args.get("show_roi", "1") == "1"
    error_ids = set()
    for mid, memo in memos.items():
        if memo.get("image_id") == image_id and memo.get("type") != "missing":
            error_ids.add(mid)
    dataset.prefetch_overlay(image_id, show_roi=show_roi, error_ann_ids=error_ids)
    return jsonify({"ok": True})


@app.route("/api/memos")
def get_memos():
    """Return all memos."""
    return jsonify(list(memos.values()))


@app.route("/api/export_memos", methods=["POST"])
def export_memos_route():
    """Export all memos to XLSX."""
    if not memos:
        return jsonify({"error": "No memos to export"}), 400

    xlsx_buf = export_memos_xlsx(dataset, memos)
    return send_file(
        xlsx_buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"review_memos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
    )


if __name__ == "__main__":
    print("=" * 50)
    print("  Mask Review Tool v2")
    print("  http://localhost:5000        (기존 검수)")
    print("  http://localhost:5000/review  (이미지 리뷰)")
    print("  Quit: Ctrl+C")
    print("=" * 50)
    try:
        import waitress
        print("  Server: waitress (production)")
        waitress.serve(app, host="0.0.0.0", port=5000, threads=4)
    except ImportError:
        print("  Server: Flask dev (install waitress for better performance)")
        app.run(host="0.0.0.0", port=5000, threaded=True)
