"""Controlled engineering fixtures, NOT proof of real reference reconstruction."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"
sys.path.insert(0,str(SCRIPTS))
import analyze
import measure
import sync
import audio
import project as lifecycle
from _common import run,sha256


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"FFmpeg/ffprobe required")
class AnalysisCompareTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def media(self,name="fixture.mkv",rate="30000/1001",frames=9,color="white",extra=None):
        path=self.root/name
        command=["ffmpeg","-v","error","-nostdin","-f","lavfi","-i",f"color=c={color}:size=64x48:rate={rate}","-frames:v",str(frames)]
        if extra:
            command+=extra
        command += ["-c:v","ffv1","-fps_mode","passthrough",str(path)]
        run(command)
        return path

    def test_full_decode_original_pts_fractional_fps(self):
        source=self.media()
        project=self.root/"project"
        project.mkdir()
        cfg={"mode":"match","reference":{"path":str(source),"sha256":sha256(source)},"output":{"width":64,"height":48,"frames":9,"fps":{"num":30000,"den":1001}}}
        analyze.write_json(project/"project.json",cfg)
        with redirect_stdout(io.StringIO()):
            report=analyze.analyze(project)
        self.assertEqual(report["frames"],9)
        self.assertEqual(report["fps"],{"num":30000,"den":1001})
        self.assertEqual(report["decode"]["coverage"],1)
        self.assertEqual(len(list((project/"ref/frames").glob("*.png"))),9)
        self.assertEqual(report["index"][0]["frame"],0)
        self.assertTrue(all(a["pts"]<b["pts"] for a,b in zip(report["index"],report["index"][1:])))
        self.assertEqual(report["claims"]["observedByAgent"],"unverified")
        self.assertEqual(json.loads((project/"ref/audio-analysis.json").read_text())["status"],"not_applicable")
        # Identical rerun is safe, without rewriting original evidence.
        with redirect_stdout(io.StringIO()):
            analyze.analyze(project)

    def test_create_does_not_manufacture_reference(self):
        analyze.write_json(self.root/"project.json",{"mode":"create","reference":None})
        with self.assertRaisesRegex(ValueError,"actual reference"):
            analyze.analyze(self.root)
        self.assertFalse((self.root/"ref").exists())

    def test_pending_match_init_analyzes_and_audio_entry_is_idempotent(self):
        source=self.media(rate="24",frames=3)
        for audio_first in (False,True):
            with self.subTest(audio_first=audio_first):
                workspace=self.root/("audio-first" if audio_first else "video-first")
                project=workspace/"projects/match-fixture"
                with redirect_stdout(io.StringIO()):
                    lifecycle.init(argparse.Namespace(workspace=str(workspace),id="match-fixture",mode="match",ref=str(source)))
                    if audio_first:
                        audio.analyze_reference(project)
                    metadata=analyze.analyze(project)
                    before=sha256(project/"ref/audio-analysis.json")
                    audio_report=audio.analyze_reference(project)
                    analyze.analyze(project)
                self.assertIsNone(json.loads((project/"project.json").read_text())["output"])
                self.assertEqual(metadata["frames"],3)
                self.assertEqual(audio_report["referenceSha256"],sha256(source))
                self.assertEqual(audio_report["status"],"not_applicable")
                self.assertEqual(sha256(project/"ref/audio-analysis.json"),before)

    def test_unsigned_cut_difference_regression(self):
        first,second=self.root/"a.png",self.root/"b.png"
        Image.new("RGB",(4,4),"white").save(first)
        Image.new("RGB",(4,4),"black").save(second)
        self.assertEqual(analyze.frame_differences([first,second])[1]["meanAbsoluteDifference"],255)

    def test_equivalent_rational_rates_are_not_false_mismatches(self):
        self.assertTrue(analyze.same_rate({"num":48,"den":2},{"num":24,"den":1}))
        self.assertFalse(analyze.same_rate({"num":30,"den":1},{"num":30000,"den":1001}))

    def test_reject_vfr_without_normalization(self):
        source=self.media(rate="25",frames=9,extra=["-vf","select='not(eq(n,4))'"])
        with self.assertRaisesRegex(ValueError,"VFR|timestamps"):
            analyze.inspect_video(source)

    def test_reject_hdr_rotation_non_square_metadata(self):
        source=self.media(rate="24",frames=2)
        actual=analyze.probe(source)
        for update,message in [({"color_transfer":"smpte2084"},"HDR"),({"sample_aspect_ratio":"4:3"},"Non-square"),({"tags":{"rotate":"90"}},"Rotated")]:
            modified=json.loads(json.dumps(actual))
            modified["streams"][0].update(update)
            with patch.object(analyze,"probe",return_value=modified):
                with self.assertRaisesRegex(ValueError,message):
                    analyze.inspect_video(source)

    def test_reference_contract_mismatch_not_resampled(self):
        source=self.media(rate="24",frames=3)
        analyze.write_json(self.root/"project.json",{"mode":"match","reference":{"path":str(source),"sha256":sha256(source)},"output":{"width":128,"height":48,"frames":3,"fps":{"num":24,"den":1}}})
        with self.assertRaisesRegex(ValueError,"contract mismatch"):
            analyze.analyze(self.root)
        self.assertFalse((self.root/"ref").exists())

    def test_track_measures_pixels_and_preserves_missing(self):
        pixels=np.zeros((30,40,3),dtype=np.uint8)
        pixels[7:12,11:18]=[220,30,40]
        detector={"roi":[0,0,40,30],"color":[220,30,40],"tolerance":0,"minPixels":4,"component":"largest"}
        image=Image.fromarray(pixels)
        self.assertEqual(measure.detect(image,detector),{"x":11,"y":7,"w":7,"h":5,"pixels":35})
        image.save(self.root/"000000.png")
        Image.new("RGB",(40,30),"black").save(self.root/"000001.png")
        measured=measure.observe(self.root,[0,3],detector,(40,30))
        self.assertIsNone(measured[1]["bbox"])
        self.assertIsNone(measured[2]["bbox"])
        self.assertEqual(measured[2]["reason"],"missing frame")
        with self.assertRaisesRegex(ValueError,"ROI"):
            measure.detect(image,{**detector,"roi":[-1,0,40,30]})

    def test_maximum_per_axis_not_average_and_coverage(self):
        refs=[{"frame":i,"bbox":{"x":0,"y":0,"w":10,"h":10}} for i in range(5)]
        actual=json.loads(json.dumps(refs))
        actual[3]["bbox"]["x"]=2
        report=measure.compare_tracks(refs,actual,100,100)
        self.assertEqual(report["status"],"fail")
        self.assertEqual(report["maximum"]["x"],{"error":0.02,"frame":3})
        actual[3]["bbox"]=None
        report=measure.compare_tracks(refs,actual,100,100)
        self.assertEqual(report["status"],"unverified")
        self.assertEqual(report["coverage"],0.8)
        self.assertIsNone(report["observations"][3]["error"])

    def test_native_pair_never_scales_pixels(self):
        first=Image.new("RGB",(64,48),"red")
        second=Image.new("RGB",(80,60),"blue")
        paired=sync.paired_image(first,second,"before","after")
        self.assertEqual(paired.size,(144,92))
        self.assertEqual(paired.getpixel((63,79)),(255,0,0))
        self.assertEqual(paired.getpixel((64,91)),(0,0,255))
        self.assertEqual(paired.getpixel((0,91)),(21,23,28))

    def test_revision_duration_mismatch_is_labelled_without_stretch(self):
        before=self.media("before.mkv",rate="10",frames=3,color="red")
        after=self.media("after.mkv",rate="10",frames=5,color="blue")
        project=self.root/"project"
        project.mkdir()
        # Snapshot validation has separate project tests. Here isolate real video decoding/comparison.
        with patch.object(sync,"load_revision",return_value=(project,{}, {},{"fingerprint":"controlled-test"})):
            with redirect_stdout(io.StringIO()):
                report=sync.revision_comparison(project,"r001",before,after)
        self.assertEqual(report["durationDifferenceSeconds"],0.2)
        self.assertEqual(report["encoded"]["frames"],5)
        self.assertEqual([r["beforeFrame"] for r in report["mapping"]],[0,1,2,None,None])
        self.assertEqual([r["afterFrame"] for r in report["mapping"]],[0,1,2,3,4])
        self.assertEqual(report["perceptualReview"],"unverified")

    def test_existing_evidence_cannot_be_replaced_or_partially_published(self):
        src,dst=self.root/"source",self.root/"dest"
        src.mkdir();dst.mkdir()
        (src/"a").write_text("new")
        (src/"z").write_text("changed")
        (dst/"z").write_text("original")
        with self.assertRaisesRegex(ValueError,"different evidence"):
            analyze.publish_tree(src,dst)
        self.assertFalse((dst/"a").exists())
        self.assertEqual((dst/"z").read_text(),"original")


if __name__=="__main__":
    unittest.main()
