#!/usr/bin/env python3
"""Measure pixels in explicit ROIs; retain missing observations and maximum errors."""
import argparse
import json
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image, ImageDraw

from _common import cli_main, ident, load_revision, read_json, revision_output, safe_path, sha256
from analyze import publish_tree, same_contract, write_json


def parse_ints(value, count):
    parts = [int(part) for part in value.split(",")]
    if len(parts) != count:
        raise ValueError(f"Expected {count} comma-separated integers")
    return parts


def parse_range(value):
    parts = [int(part) for part in value.split(":")]
    if len(parts) != 2 or parts[0] < 0 or parts[1] <= parts[0]:
        raise ValueError("Frame range must be a non-empty half-open A:B interval")
    return parts


def validate_detector(detector, size):
    x, y, w, h = detector["roi"]
    if any(type(v) is not int for v in detector["roi"]) or x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > size[0] or y+h > size[1]:
        raise ValueError("ROI must be an integer X,Y,W,H rectangle wholly inside the frame")
    if len(detector["color"]) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in detector["color"]):
        raise ValueError("RGB color must contain three byte values")
    if not 0 <= detector.get("tolerance", 20) <= 255 or detector.get("minPixels", 4) < 1:
        raise ValueError("Invalid RGB tolerance or minimum component area")
    if detector.get("component", "largest") not in ("largest", "all"):
        raise ValueError("component must be largest or all")


def detect(image, detector):
    """Threshold RGB; largest uses 4-connected components. Never interpolate."""
    validate_detector(detector, image.size)
    x, y, w, h = detector["roi"]
    pixels = np.asarray(image.convert("RGB"), dtype=np.int16)[y:y+h, x:x+w]
    mask = np.all(np.abs(pixels - np.array(detector["color"], dtype=np.int16)) <= detector.get("tolerance", 20), axis=2)
    if detector.get("component", "largest") == "largest":
        seen = np.zeros(mask.shape, dtype=bool)
        best = []
        for sy, sx in zip(*np.nonzero(mask)):
            if seen[sy, sx]:
                continue
            stack, current = [(int(sy), int(sx))], []
            seen[sy, sx] = True
            while stack:
                cy, cx = stack.pop()
                current.append((cy, cx))
                for ny, nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny,nx] and not seen[ny,nx]:
                        seen[ny,nx] = True
                        stack.append((ny,nx))
            if len(current) > len(best):
                best = current
        ys = [p[0] for p in best]
        xs = [p[1] for p in best]
    else:
        ys, xs = np.nonzero(mask)
    if len(xs) < detector.get("minPixels", 4):
        return None
    return {"x": int(min(xs))+x, "y": int(min(ys))+y, "w": int(max(xs)-min(xs)+1), "h": int(max(ys)-min(ys)+1), "pixels": len(xs)}


def observe(directory, interval, detector, size):
    rows = []
    for frame in range(*interval):
        path = Path(directory) / f"{frame:06d}.png"
        if not path.is_file():
            rows.append({"frame": frame, "bbox": None, "reason": "missing frame", "imageSha256": None})
            continue
        with Image.open(path) as image:
            if image.size != tuple(size):
                raise ValueError(f"Unexpected image dimensions: {path}")
            bbox = detect(image, detector)
        rows.append({"frame": frame, "bbox": bbox, "reason": None if bbox else "threshold detector found no qualifying component; occlusion/identity unknown", "imageSha256": sha256(path)})
    return rows


def compare_tracks(reference, output, width, height, threshold=0.01):
    if [r["frame"] for r in reference] != [r["frame"] for r in output]:
        raise ValueError("Track frame ranges differ; no automatic temporal alignment")
    maximum = {k: {"error": None, "frame": None} for k in ("x","y","w","h")}
    rows, valid = [], 0
    for ref, actual in zip(reference, output):
        if ref["bbox"] is None or actual["bbox"] is None:
            rows.append({"frame": ref["frame"], "error": None, "reason": "reference or output observation missing"})
            continue
        valid += 1
        errors = {k: abs(actual["bbox"][k] - ref["bbox"][k]) / (width if k in ("x","w") else height) for k in maximum}
        for key, value in errors.items():
            if maximum[key]["error"] is None or value > maximum[key]["error"]:
                maximum[key] = {"error": value, "frame": ref["frame"]}
        rows.append({"frame": ref["frame"], "error": errors})
    coverage = valid / len(reference) if reference else 0.0
    exceeded = any(v["error"] is not None and v["error"] > threshold for v in maximum.values())
    return {"status": "fail" if exceeded else ("pass" if coverage == 1.0 else "unverified"), "thresholdPerAxis": threshold, "maximum": maximum,
            "validFrames": valid, "requestedFrames": len(reference), "coverage": coverage, "observations": rows}


def track(args):
    project = args.project.resolve(strict=True)
    reference_root = safe_path(project,"ref")
    detector = {"roi": parse_ints(args.roi, 4), "color": parse_ints(args.color, 3), "tolerance": args.tolerance, "minPixels": args.min_pixels, "component": args.component}
    interval = parse_range(args.range)
    ident(args.object)
    if args.source == "reference":
        cfg = read_json(project/"project.json")
        if cfg.get("mode") != "match" or not cfg.get("reference"):
            raise ValueError("Reference tracking requires match and an actual analyzed reference")
        index = read_json(safe_path(reference_root,"index.json"))
        if index["sourceSha256"] != cfg["reference"]["sha256"]:
            raise ValueError("Reference analysis belongs to a different reference")
        size, count = (index["width"], index["height"]), index["frames"]
        directory, destination = safe_path(project,"ref/frames"), safe_path(project,"ref/tracks")
        provenance = {"source": "reference", "sourceSha256": index["sourceSha256"]}
    else:
        if not args.revision:
            raise ValueError("Rendered tracking requires --revision")
        _, cfg, _, manifest = load_revision(project, args.revision)
        size, count = (cfg["output"]["width"],cfg["output"]["height"]), cfg["output"]["frames"]
        directory = revision_output(project,args.revision)/"frames"
        destination = revision_output(project,args.revision)/"review/tracks"
        provenance = {"source": "rendered", "revision": args.revision, "fingerprint": manifest["fingerprint"]}
    if interval[1] > count:
        raise ValueError("Track interval exceeds available frame count")
    validate_detector(detector, size)
    observations = observe(directory, interval, detector, size)
    valid = sum(row["bbox"] is not None for row in observations)
    data = {"kind":"track", "measuredProperties":["x","y","w","h"], "referenceSha256": provenance.get("sourceSha256"), "object": args.object, **provenance, "method": "RGB threshold/4-connected components", "detector": detector, "range": interval,
            "fullVisibleRangeDeclared": args.full_visible_range, "declarationBasis": args.basis,
            "size": list(size), "observations": observations, "coverage": valid / len(observations), "interpolated": False,
            "identityAndOcclusionReview": "unverified", "toolSha256": sha256(Path(__file__))}
    with tempfile.TemporaryDirectory(prefix=".track-",dir=project) as temp:
        write_json(Path(temp)/(args.object+".json"),data)
        chosen = sorted(set([0,len(observations)//2,len(observations)-1]))
        for position in chosen:
            row=observations[position]
            file=directory/f"{row['frame']:06d}.png"
            if not file.exists():
                continue
            with Image.open(file) as img:
                img=img.convert("RGB")
                drawing=ImageDraw.Draw(img)
                if row["bbox"]:
                    b=row["bbox"]
                    drawing.rectangle((b["x"],b["y"],b["x"]+b["w"]-1,b["y"]+b["h"]-1),outline="yellow",width=1)
                drawing.text((4,4),f"{args.object} F{row['frame']:06d} detected={row['bbox'] is not None}",fill="white")
                img.save(Path(temp)/f"{args.object}-{row['frame']:06d}.png")
        publish_tree(temp,destination)
    print(json.dumps({"track":str(destination/(args.object+".json")),"coverage":data["coverage"],"semanticReview":"unverified"}))


def verify_frame_evidence(out, manifest, count, selected=None):
    render = read_json(out/"review/render.json")
    if render["fingerprint"] != manifest["fingerprint"]:
        raise ValueError("Render evidence belongs to another input snapshot")
    for frame in (range(count) if selected is None else selected):
        file = out/"frames"/f"{frame:06d}.png"
        expected = render["frames"].get(str(frame))
        if not expected or not file.is_file() or sha256(file) != expected:
            raise ValueError(f"Missing, stale or altered rendered frame {frame}")


def check(args):
    project = args.project.resolve(strict=True)
    reference_root = safe_path(project,"ref")
    _, cfg, timeline, manifest = load_revision(project,args.revision)
    out=revision_output(project,args.revision)
    o=cfg["output"]
    verify_frame_evidence(out,manifest,o["frames"])
    results=[]
    if cfg["mode"] == "match":
        index=read_json(safe_path(reference_root,"index.json"))
        if index["sourceSha256"] != cfg["reference"]["sha256"]:
            raise ValueError("Reference analysis hash mismatch")
        if not same_contract(index,o):
            raise ValueError("Reference/output geometry or timing differ; no automatic alignment")
        threshold=cfg.get("measurementTolerance",0.01)
        if not 0 <= threshold <= 0.01:
            raise ValueError("measurementTolerance may tighten, but not loosen, the 1% per-axis match requirement")
        for file in sorted(safe_path(project,"ref/tracks").glob("*.json")):
            file=safe_path(project,file.relative_to(project))
            ref=read_json(file)
            if ref["sourceSha256"] != index["sourceSha256"]:
                raise ValueError("Track source hash mismatch")
            for row in ref["observations"]:
                p=safe_path(project,f"ref/frames/{row['frame']:06d}.png")
                if row["imageSha256"] and (not p.exists() or sha256(p)!=row["imageSha256"]):
                    raise ValueError("Reference pixel evidence changed")
            observations=observe(out/"frames",ref["range"],ref["detector"],(o["width"],o["height"]))
            comparison=compare_tracks(ref["observations"],observations,o["width"],o["height"],threshold)
            comparison.update({"object":ref["object"],"trackEvidence":str(file.relative_to(project)),"trackSha256":sha256(file),"outputObservations":observations,"fullVisibleRangeDeclared":ref.get("fullVisibleRangeDeclared",False)})
            if comparison["status"] == "pass" and not ref.get("fullVisibleRangeDeclared"):
                comparison["status"]="unverified"
                comparison["note"]="Measured range passed; complete visible range was not declared"
            results.append(comparison)
    else:
        for rule in cfg.get("measurementChecks",[]):
            ident(rule["id"])
            interval=rule.get("range",[0,o["frames"]])
            if len(interval)!=2 or not 0<=interval[0]<interval[1]<=o["frames"]:
                raise ValueError("Invalid create measurement range")
            if not rule.get("bounds"):
                raise ValueError("Create measurement needs explicit pixel bounds")
            rows=observe(out/"frames",interval,rule["detector"],(o["width"],o["height"]))
            failures=[]
            for row in rows:
                if row["bbox"] is None:
                    continue
                for key,limits in rule["bounds"].items():
                    if key not in ("x","y","w","h") or len(limits)!=2 or limits[0]>limits[1]:
                        raise ValueError("Bounds use x/y/w/h and inclusive [minimum,maximum] pixel pairs")
                    if not limits[0]<=row["bbox"][key]<=limits[1]:
                        failures.append({"frame":row["frame"],"axis":key,"actual":row["bbox"][key],"bounds":limits})
            coverage=sum(r["bbox"] is not None for r in rows)/len(rows)
            results.append({"id":rule["id"],"status":"fail" if failures else ("pass" if coverage==1 else "unverified"),"coverage":coverage,"failures":failures,"observations":rows})
    # The timeline describes intent. It is never itself evidence that an event happened.
    events=[{"id":e["id"],"status":"unverified","reason":"Planned anchor alone is not an observed visual event; supply review or task-specific pixel detector evidence"} for e in timeline.get("events",[])]
    status="fail" if any(r["status"]=="fail" for r in results) else ("pass" if results and all(r["status"]=="pass" for r in results) else "unverified")
    report={"revision":args.revision,"fingerprint":manifest["fingerprint"],"mode":cfg["mode"],"numericStatus":status,"objects":results,"events":events,
            "lockedEventStatus":"unverified" if events or cfg.get("locks") else "not_applicable", "perceptualStatus":"unverified",
            "overallStatus":"fail" if status=="fail" else "unverified", "note":"Numeric coverage is limited to explicit detectors and declared ranges. Does not establish semantic identity, content, text, camera, sound, or user acceptance.","toolSha256":sha256(Path(__file__))}
    with tempfile.TemporaryDirectory(prefix=".measure-",dir=project) as temporary:
        write_json(Path(temporary)/"measurements.json",report)
        publish_tree(temporary,out/"review")
    print(json.dumps({"report":str(out/"review/measurements.json"),"numericStatus":status,"overallStatus":report["overallStatus"]}))
    if status == "fail":
        raise ValueError("Observed pixel measurement exceeds project bounds; evidence saved")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest="operation",required=True)
    track_parser=actions.add_parser("track",help="Measure explicitly selected RGB components in every requested frame; no interpolation")
    track_parser.add_argument("--project",required=True,type=Path)
    track_parser.add_argument("--object",required=True,help="Stable object ID for immutable measurement evidence")
    track_parser.add_argument("--range",required=True,help="Half-open frame interval A:B")
    track_parser.add_argument("--roi",required=True,help="X,Y,W,H integer pixels; must contain the full visible trajectory")
    track_parser.add_argument("--color",required=True,help="R,G,B threshold target selected from actual pixels")
    track_parser.add_argument("--tolerance",type=int,default=20,help="Maximum absolute difference per RGB channel")
    track_parser.add_argument("--min-pixels",type=int,default=4)
    track_parser.add_argument("--component",choices=("largest","all"),default="largest")
    track_parser.add_argument("--source",choices=("reference","rendered"),default="reference")
    track_parser.add_argument("--revision",help="Required when --source rendered")
    track_parser.add_argument("--full-visible-range",action="store_true",help="Declare that --range includes all visible frames of this object; this declaration is not automatic observation")
    track_parser.add_argument("--basis",default="explicit CLI range",help="Evidence or reason supporting the chosen range and detector")
    check_parser=actions.add_parser("check",help="Compare actual render pixels with stored reference tracks, or explicit create measurementChecks")
    check_parser.add_argument("--project",required=True,type=Path)
    check_parser.add_argument("--revision",required=True)
    args=parser.parse_args()
    track(args) if args.operation=="track" else check(args)


if __name__=="__main__":
    cli_main(main)
