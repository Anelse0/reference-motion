#!/usr/bin/env python3
"""Index actual CFR reference media without silently changing its timing."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

from _common import cli_main, probe, read_json, run, safe_path, sha256


def write_json(path, value, sort_keys=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=sort_keys, allow_nan=False) + "\n", encoding="utf-8")


def same_rate(a, b):
    return Fraction(a["num"], a["den"]) == Fraction(b["num"], b["den"])


def same_contract(reference, output):
    return all(reference[key] == output[key] for key in ("width", "height", "frames")) and same_rate(reference["fps"], output["fps"])


def publish_tree(source, destination):
    """Retry only identical outputs; keep unrelated existing review artifacts."""
    source, destination = Path(source), Path(destination)
    files = sorted(p for p in source.rglob("*") if p.is_file())
    for item in files:
        target = safe_path(destination, item.relative_to(source))
        if target.exists() and sha256(target) != sha256(item):
            raise ValueError(f"Refusing to replace different evidence: {target}; use a new revision or project")
    for item in files:
        target = safe_path(destination, item.relative_to(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(item, target)


def inspect_video(path):
    """Return PTS evidence, rejecting formats requiring an authorized normalization."""
    path = Path(path).resolve(strict=True)
    media = probe(path)
    streams = [s for s in media["streams"] if s.get("codec_type") == "video"]
    if not streams:
        raise ValueError("Reference has no video stream")
    stream = streams[0]
    sar = stream.get("sample_aspect_ratio", "1:1")
    if sar not in ("1:1", "0:1", "N/A", None):
        raise ValueError("Non-square pixels require explicitly authorized normalization")
    rotation = float(stream.get("tags", {}).get("rotate", 0))
    for side in stream.get("side_data_list", []):
        rotation = float(side.get("rotation", rotation))
    if rotation % 360:
        raise ValueError("Rotated video requires explicitly authorized normalization")
    if stream.get("color_transfer") in ("smpte2084", "arib-std-b67") or stream.get("color_primaries") == "bt2020":
        raise ValueError("HDR/wide-gamut reference requires explicitly authorized normalization")
    if int(stream.get("width", 0)) <= 0 or int(stream.get("height", 0)) <= 0:
        raise ValueError("Invalid video dimensions")
    try:
        fps = Fraction(stream["avg_frame_rate"])
        tick = Fraction(stream["time_base"])
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("Missing reliable rational frame rate or time base") from exc
    if fps <= 0 or tick <= 0:
        raise ValueError("Invalid frame rate or time base")
    raw = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_entries", "frame=pts,best_effort_timestamp,pkt_duration,pict_type,key_frame", "-of", "json", str(path)]).stdout)
    frames = raw.get("frames", [])
    if not frames:
        raise ValueError("Video contains no decoded frames")
    pts = []
    for f in frames:
        if f.get("pts") is None:
            raise ValueError("Missing presentation timestamp; do not infer original timing")
        pts.append(int(f["pts"]))
    first = pts[0]
    for i, value in enumerate(pts):
        if i and value <= pts[i - 1]:
            raise ValueError("Non-increasing presentation timestamps")
        if abs((value - first) * tick - Fraction(i, 1) / fps) > tick:
            raise ValueError(f"VFR/discontinuous timestamps at frame {i}; explicit normalization required")
    audio = next((s for s in media["streams"] if s.get("codec_type") == "audio"), None)
    offset = None
    if audio and audio.get("start_time") is not None:
        offset = float(Fraction(audio["start_time"]) - first * tick)
    return {"source": str(path), "sourceSha256": sha256(path), "width": int(stream["width"]), "height": int(stream["height"]),
            "fps": {"num": fps.numerator, "den": fps.denominator}, "frames": len(frames), "timeBase": str(tick),
            "firstPts": first, "durationSeconds": float(Fraction(len(frames), 1) / fps), "audioOffsetSeconds": offset,
            "hasAudio": audio is not None, "originalStream": stream,
            "index": [{"frame": i, "pts": p, "ptsSeconds": float(p * tick), "relativeSeconds": float((p-first)*tick), "file": f"frames/{i:06d}.png"} for i, p in enumerate(pts)]}


def decode_video(path, directory, expected):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-v", "error", "-nostdin", "-noautorotate", "-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough", "-start_number", "0", str(directory / "%06d.png")])
    files = sorted(directory.glob("*.png"))
    wanted = [f"{i:06d}.png" for i in range(expected["frames"])]
    if [p.name for p in files] != wanted:
        raise ValueError("Full-frame decode count/name mismatch; no dropped or duplicated frames allowed")
    for file in files:
        with Image.open(file) as image:
            if image.size != (expected["width"], expected["height"]):
                raise ValueError(f"Decoded frame dimension mismatch: {file}")
    return files


def frame_differences(files):
    previous = None
    scores = []
    for i, file in enumerate(files):
        with Image.open(file) as image:
            current = np.asarray(image.convert("RGB"), dtype=np.int16)
        score = 0.0 if previous is None else float(np.abs(current - previous).mean())
        scores.append({"frame": i, "meanAbsoluteDifference": score})
        previous = current
    return scores


def make_overview(files, metadata, target):
    fps = Fraction(metadata["fps"]["num"], metadata["fps"]["den"])
    sample = sorted(set([0] + [min(len(files)-1, int(Fraction(s) * fps)) for s in range(1, int(metadata["durationSeconds"])+1)]))
    # Pagination bounds memory for long references; labels retain actual frame numbers.
    pages = []
    thumb_w = 240
    thumb_h = max(1, round(metadata["height"] * thumb_w / metadata["width"]))
    for page, start in enumerate(range(0, len(sample), 24)):
        numbers = sample[start:start+24]
        cols, rows = min(4, len(numbers)), (len(numbers)+3)//4
        sheet = Image.new("RGB", (cols*thumb_w, rows*(thumb_h+26)), "#17191d")
        draw = ImageDraw.Draw(sheet)
        for cell, number in enumerate(numbers):
            x, y = (cell % 4)*thumb_w, (cell // 4)*(thumb_h+26)
            with Image.open(files[number]) as frame:
                sheet.paste(frame.convert("RGB").resize((thumb_w, thumb_h)), (x, y+26))
            draw.text((x+5, y+6), f"REF F{number:06d}  t={float(Fraction(number)/fps):.3f}s", fill="white")
        name = f"overview-{page:03d}.png"
        sheet.save(Path(target)/name)
        pages.append({"file": name, "frames": numbers, "displayScale": thumb_w/metadata["width"]})
    return pages


def analyze(project, threshold=24.0, skip_audio=False):
    project = Path(project).resolve(strict=True)
    reference_root = safe_path(project, "ref")
    cfg = read_json(project / "project.json")
    if cfg.get("mode") != "match" or not cfg.get("reference"):
        raise ValueError("analyze requires mode=match and an actual reference; create skips reference analysis")
    ref = cfg["reference"]
    path = Path(ref["path"])
    if not path.is_absolute():
        path = safe_path(project, path)
    if sha256(path) != ref["sha256"]:
        raise ValueError("Reference hash changed; original measurement evidence cannot be rewritten")
    metadata = inspect_video(path)
    output = cfg.get("output") or {}
    expected = (output.get("width"), output.get("height"), output.get("frames"), output.get("fps"))
    actual = (metadata["width"], metadata["height"], metadata["frames"], metadata["fps"])
    for key, desired, found in zip(("width","height","frames","fps"), expected, actual):
        equal = same_rate(desired,found) if key=="fps" and desired is not None else desired==found
        if desired is not None and not equal:
            raise ValueError(f"Project/reference contract mismatch: expected {expected}, actual {actual}")
    with tempfile.TemporaryDirectory(prefix=".analyze-", dir=project) as temporary:
        temp = Path(temporary)
        files = decode_video(path, temp/"frames", metadata)
        scores = frame_differences(files)
        metadata["decode"] = {"coverage": 1.0, "decodedFrames": len(files), "resampling": "none", "rotation": "disabled", "frameHashes": {str(i): sha256(p) for i,p in enumerate(files)}}
        metadata["overview"] = make_overview(files, metadata, temp)
        metadata["toolSha256"] = sha256(Path(__file__))
        metadata["claims"] = {"mechanicalDecode": "pass", "observedByAgent": "unverified", "faithfulReconstruction": "unverified"}
        metadata["audioAnalysis"] = "explicitly_skipped" if skip_audio else ("separate_report" if metadata["hasAudio"] else "not_applicable")
        write_json(temp/"index.json", metadata)
        write_json(temp/"cuts.json", {"method": "signed RGB mean absolute adjacent-frame difference", "threshold": threshold, "scores": scores,
                   "candidates": [s["frame"] for s in scores[1:] if s["meanAbsoluteDifference"] >= threshold],
                   "interpretation": "Candidates only; flashes, moving masks and continuous transitions require observation. No automatic hard-cut claim."})
        if not metadata["hasAudio"]:
            write_json(temp/"audio-analysis.json", {"status": "not_applicable", "referenceSha256": metadata["sourceSha256"], "reason": "No reference audio stream"}, sort_keys=True)
        publish_tree(temp, reference_root)
    if metadata["hasAudio"] and not skip_audio:
        run([sys.executable, str(Path(__file__).with_name("audio.py")), "--project", str(project), "analyze"])
    print(json.dumps({"reference": str(project/"ref"), "frames": metadata["frames"], "coverage": 1.0, "observation": "unverified"}))
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="Independent project directory in match mode")
    parser.add_argument("--cut-threshold", type=float, default=24.0, help="Signed RGB mean absolute difference candidate threshold (0..255); not a semantic cut detector")
    parser.add_argument("--skip-audio", action="store_true", help="Explicitly skip mechanical audio analysis; leave it unverified")
    args = parser.parse_args()
    if not 0 < args.cut_threshold <= 255:
        parser.error("--cut-threshold must be greater than 0 and at most 255")
    analyze(args.project, args.cut_threshold, args.skip_audio)


if __name__ == "__main__":
    cli_main(main)
