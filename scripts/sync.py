#!/usr/bin/env python3
"""Make labelled comparisons of real images, without spatial or temporal alignment."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw

from _common import cli_main, load_revision, read_json, revision_output, run, safe_path, sha256
from analyze import decode_video, inspect_video, publish_tree, same_contract, write_json
from measure import verify_frame_evidence


def paired_image(first, second, first_label, second_label, vertical=False):
    """Native-size pixels: differing dimensions are padded, never resized."""
    a, b = first, second
    band = 32
    if vertical:
        width=max(a.width,b.width)
        result=Image.new("RGB",(width,a.height+b.height+2*band),"#15171c")
        positions=[(0,band),(0,2*band+a.height)]
        labels=[(6,9),(6,band+a.height+9)]
    else:
        result=Image.new("RGB",(a.width+b.width,max(a.height,b.height)+band),"#15171c")
        positions=[(0,band),(a.width,band)]
        labels=[(6,9),(a.width+6,9)]
    result.paste(a,positions[0])
    result.paste(b,positions[1])
    drawing=ImageDraw.Draw(result)
    drawing.text(labels[0],first_label,fill="white")
    drawing.text(labels[1],second_label,fill="white")
    return result


def read_frame(path):
    with Image.open(path) as image:
        return image.convert("RGB")


def encode_comparison(directory, output, fps, count):
    with Image.open(Path(directory)/"000000.png") as first:
        width,height=first.size
    # Odd dimensions are preserved using yuv444p; the review never crops a pixel.
    pixel_format="yuv420p" if width%2==0 and height%2==0 else "yuv444p"
    run(["ffmpeg","-v","error","-nostdin","-framerate",f"{fps.numerator}/{fps.denominator}","-start_number","0","-i",str(Path(directory)/"%06d.png"),
         "-frames:v",str(count),"-an","-c:v","libx264","-crf","15","-pix_fmt",pixel_format,"-fps_mode","passthrough","-movflags","+faststart",str(output)])
    result=inspect_video(output)
    if result["frames"]!=count or result["fps"]!={"num":fps.numerator,"den":fps.denominator}:
        raise ValueError("Comparison encode frame count/fps mismatch")
    return {"frames":count,"fps":result["fps"],"dimensions":[width,height],"sha256":sha256(output),"audio":"none; visual comparison only"}


def reference_comparison(project,revision,selected=None):
    _,cfg,timeline,manifest=load_revision(project,revision)
    if cfg["mode"]!="match" or not cfg.get("reference"):
        raise ValueError("Reference comparison requires match and an actual reference. Create has no invented REF.")
    out=revision_output(project,revision)
    index=read_json(safe_path(project,"ref/index.json"))
    o=cfg["output"]
    if index["sourceSha256"]!=cfg["reference"]["sha256"] or not same_contract(index,o):
        raise ValueError("Reference contract differs from output; cannot hide differences with alignment or scaling")
    if selected is not None and (not selected or len(selected)>15 or any(type(f) is not int or f<0 or f>=o["frames"] for f in selected)):
        raise ValueError("--frames requires 1 to 15 valid integer frame numbers")
    verify_frame_evidence(out,manifest,o["frames"],selected)
    fps=Fraction(o["fps"]["num"],o["fps"]["den"])
    seconds=sorted(set(int(Fraction(s)*fps) for s in range(int(Fraction(o["frames"],1)/fps)+1) if int(Fraction(s)*fps)<o["frames"]))
    seams=set()
    for shot in timeline["shots"][1:]:
        seams.update(range(max(0,shot["f0"]-2),min(o["frames"],shot["f0"]+3)))
    for transition in timeline.get("transitions",[]):
        seams.update(range(transition["f0"],transition["f1"]))
    with tempfile.TemporaryDirectory(prefix=".sync-",dir=project) as temporary:
        root=Path(temporary)
        film=root/"film"
        deliver=root/"deliver"
        film.mkdir()
        (deliver/"review/reference-pairs").mkdir(parents=True)
        mappings=[]
        for frame in (range(o["frames"]) if selected is None else selected):
            ref_path=safe_path(project,f"ref/frames/{frame:06d}.png")
            if not ref_path.is_file() or sha256(ref_path)!=index["decode"]["frameHashes"][str(frame)]:
                raise ValueError(f"Reference pixel evidence is missing or changed at frame {frame}")
            ref=read_frame(ref_path)
            rendered=read_frame(out/"frames"/f"{frame:06d}.png")
            labels=(f"REFERENCE F{frame:06d} (original frame)",f"OUTPUT {revision} F{frame:06d}")
            if selected is None:
                paired_image(ref,rendered,*labels,vertical=True).save(film/f"{frame:06d}.png")
            if selected is not None or frame in seconds or frame in seams:
                paired_image(ref,rendered,*labels).save(deliver/"review/reference-pairs"/f"{frame:06d}.png")
            mappings.append({"frame":frame,"referenceFrame":frame,"outputFrame":frame,"relativeSeconds":float(Fraction(frame)/fps)})
        encoded=encode_comparison(film,deliver/"comparison.mp4",fps,o["frames"]) if selected is None else None
        report={"kind":"reference","revision":revision,"fingerprint":manifest["fingerprint"],"referenceSha256":index["sourceSha256"],"encoded":encoded,
                "coverage":len(mappings)/o["frames"],"mapping":mappings,"perSecondFrames":seconds if selected is None else [],"seamAndTransitionFrames":sorted(seams) if selected is None else [],
                "transform":"none; native-size pixels, identical frame indices", "perceptualReview":"unverified","toolSha256":sha256(Path(__file__))}
        report_name="comparison.json" if selected is None else "comparison-stills-"+"-".join(str(f) for f in selected)+".json"
        write_json(deliver/"review"/report_name,report)
        publish_tree(deliver,out)
    print(json.dumps({"comparison":str(out/("comparison.mp4" if selected is None else "review/reference-pairs")),"kind":"reference","coverage":report["coverage"],"perceptualReview":"unverified"}))
    return report


def revision_comparison(project,revision,before,after):
    _,_,_,manifest=load_revision(project,revision)
    if not before or not after:
        raise ValueError("Revision comparison requires actual --before and --after video paths")
    before,after=Path(before).resolve(strict=True),Path(after).resolve(strict=True)
    first,second=inspect_video(before),inspect_video(after)
    fps1=Fraction(first["fps"]["num"],first["fps"]["den"])
    fps2=Fraction(second["fps"]["num"],second["fps"]["den"])
    # Review samples the original timeline at the higher input rate; source timestamps never stretch.
    fps=max(fps1,fps2)
    duration=max(Fraction(first["frames"])/fps1,Fraction(second["frames"])/fps2)
    count=(duration*fps).__ceil__()
    out=revision_output(project,revision)
    with tempfile.TemporaryDirectory(prefix=".revision-sync-",dir=project) as temporary:
        root=Path(temporary)
        decode_video(before,root/"before",first)
        decode_video(after,root/"after",second)
        (root/"film").mkdir()
        (root/"deliver/review").mkdir(parents=True)
        mappings=[]
        for frame in range(count):
            time=Fraction(frame)/fps
            a,b=int(time*fps1),int(time*fps2)
            a=a if a<first["frames"] else None
            b=b if b<second["frames"] else None
            im_a=read_frame(root/"before"/f"{a:06d}.png") if a is not None else Image.new("RGB",(first["width"],first["height"]),"#15171c")
            im_b=read_frame(root/"after"/f"{b:06d}.png") if b is not None else Image.new("RGB",(second["width"],second["height"]),"#15171c")
            label_a=f"BEFORE {'F'+str(a) if a is not None else 'ENDED'}  t={float(time):.3f}s"
            label_b=f"AFTER {'F'+str(b) if b is not None else 'ENDED'}  t={float(time):.3f}s"
            paired_image(im_a,im_b,label_a,label_b).save(root/"film"/f"{frame:06d}.png")
            mappings.append({"reviewFrame":frame,"seconds":float(time),"beforeFrame":a,"afterFrame":b})
        encoded=encode_comparison(root/"film",root/"deliver/comparison-revision.mp4",fps,count)
        report={"kind":"revision","revision":revision,"fingerprint":manifest["fingerprint"],"before":{"path":str(before),"sha256":first["sourceSha256"],"fps":first["fps"],"frames":first["frames"],"durationSeconds":float(Fraction(first["frames"])/fps1)},
                "after":{"path":str(after),"sha256":second["sourceSha256"],"fps":second["fps"],"frames":second["frames"],"durationSeconds":float(Fraction(second["frames"])/fps2)},
                "durationDifferenceSeconds":float(Fraction(second["frames"])/fps2-Fraction(first["frames"])/fps1),"encoded":encoded,"mapping":mappings,
                "transform":"Native-size pixels padded if needed; source frame active at each review timestamp; ended sources blank. No time stretch or motion interpolation.",
                "claim":"Version comparison only; not reference fidelity evidence","perceptualReview":"unverified","toolSha256":sha256(Path(__file__))}
        write_json(root/"deliver/review/comparison-revision.json",report)
        publish_tree(root/"deliver",out)
    print(json.dumps({"comparison":str(out/"comparison-revision.mp4"),"kind":"revision","durationDifferenceSeconds":report["durationDifferenceSeconds"]}))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",required=True,type=Path)
    parser.add_argument("--revision",required=True,help="Frozen project revision owning this evidence")
    parser.add_argument("--kind",required=True,choices=("reference","revision"))
    parser.add_argument("--before",type=Path,help="Actual before MP4 for revision comparison")
    parser.add_argument("--after",type=Path,help="Actual after MP4 for revision comparison")
    parser.add_argument("--frames",help="Comma-separated 1 to 15 frame numbers: produce selected still pairs only, with honest partial coverage")
    args=parser.parse_args()
    project=args.project.resolve(strict=True)
    if args.kind=="reference":
        if args.before or args.after:
            parser.error("--before/--after are only valid for --kind revision")
        selected=sorted(set(int(v) for v in args.frames.split(","))) if args.frames else None
        reference_comparison(project,args.revision,selected)
    else:
        if args.frames:
            parser.error("--frames is available only for reference still comparison")
        revision_comparison(project,args.revision,args.before,args.after)


if __name__=="__main__":
    cli_main(main)
