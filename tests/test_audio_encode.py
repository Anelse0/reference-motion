"""Controlled synthetic tests; neither real-reference skill nor listening approval."""
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from _common import duration_samples, sha256, tool_hashes
from audio import (Synth, audit_score, build_cues, immutable_json, normalize,
                   read_pcm, signal_checks, synth_project, write_wav, loudness)
from encode import audio_alignment, final, preview, validate_qa, verify_frames


def config(frames=48, fps=None):
    return {'id': 'fixture', 'mode': 'create', 'revision': 'r001', 'reference': None,
            'output': {'width': 64, 'height': 64, 'frames': frames, 'fps': fps or {'num': 24, 'den': 1},
                       'colorPolicy': 'srgb-to-bt709-limited'},
            'audio': {'source': 'procedural', 'sampleRate': 48000, 'channels': 2,
                      'loudnessTarget': -14, 'truePeakMax': -1, 'narrationRequired': False},
            'renderEntry': 'src/main.js', 'scoreEntry': 'audio/score.py', 'seed': 12}


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.synth = Synth(48000, 2, 96000, 42)

    def test_noise_is_event_stable(self):
        a = self.synth.noise(128, 'a')
        self.synth.noise(180, 'new')
        np.testing.assert_array_equal(a, self.synth.noise(128, 'a'))
        self.assertFalse(np.array_equal(a, self.synth.noise(128, 'b')))

    def test_integrated_frequency_and_nyquist(self):
        result = self.synth.oscillator(np.array([100., 200., 400.]))
        np.testing.assert_allclose(result, np.sin(2*np.pi*np.array([0,100,300])/48000))
        with self.assertRaises(ValueError): self.synth.oscillator(24000, 5)

    def test_band_limited_square(self):
        signal = self.synth.oscillator(8000, 480, wave='square')
        np.testing.assert_allclose(signal, 4/np.pi*self.synth.oscillator(8000, 480), atol=1e-12)

    def test_envelope_and_delay_keep_tail(self):
        signal = self.synth.envelope(480, attack=.002, release=.004)
        self.assertEqual(signal[0], 0)
        self.assertLess(signal[-1], 1e-20)
        tail = self.synth.delay(signal, 100, repeats=2)
        self.assertEqual(len(tail), 680)
        self.assertGreater(float(np.max(tail[-100:])), 0)

    def test_cue_measures_actual_onset_and_peak(self):
        track = self.synth.track()
        wave = np.array([0., 0., .2, .5, 0.])
        event = self.synth.place(track, wave, 48000, 'arrival')
        self.assertEqual(event['onsetSample'], 48002)
        self.assertEqual(event['peakSample'], 48003)
        timeline = {'events': [{'id': 'arrival', 'seconds': 1}]}
        result = build_cues(self.synth.events, timeline, config(), track)
        self.assertEqual(result['events'][0]['deltaSamples'], 2)
        self.assertEqual(result['status'], 'pass')
        timeline['events'][0]['seconds'] = .5
        self.assertEqual(build_cues(self.synth.events, timeline, config(), track)['status'], 'fail')

    def test_late_tail_and_silent_cues_rejected(self):
        with self.assertRaises(ValueError): self.synth.place(self.synth.track(), np.ones(4), 95999, 'a')
        with self.assertRaises(ValueError): self.synth.place(self.synth.track(), np.zeros(4), 0, 'a')

    def test_missing_required_event_rejected(self):
        cfg = config(); cfg['audio']['requiredEvents'] = ['missing']
        with self.assertRaises(ValueError): build_cues([], {'events': []}, cfg, self.synth.track())

    def test_rational_duration(self):
        self.assertEqual(duration_samples(config(30, {'num': 30000, 'den': 1001})), 48048)
        self.assertEqual(duration_samples(config(1, {'num': 30000, 'den': 1001})), 1602)

    def test_numeric_failures_not_listening(self):
        report = signal_checks(np.ones((96000, 2)), config())
        self.assertIn('clipping', report['failures'])
        self.assertIn('dc_offset', report['failures'])
        self.assertIn('unclosed_tail', report['failures'])
        self.assertEqual(report['listening'], 'unverified')

    def test_actual_alignment_detects_offset(self):
        rng = np.random.default_rng(12)
        source = rng.normal(0,.1,(5000,2)); decoded = np.zeros_like(source)
        decoded[37:] = source[:-37]
        report = audio_alignment(source, decoded, 100)
        self.assertEqual(report['measuredLagSamples'],37)
        self.assertEqual(report['status'],'pass')

    def test_stereo_anticorrelation_does_not_look_silent(self):
        signal=np.random.default_rng(3).normal(0,.1,5000)
        source=np.column_stack((signal,-signal))
        report=audio_alignment(source,source,100)
        self.assertEqual(report['status'],'pass')
        self.assertEqual(report['measuredLagSamples'],0)

    def test_import_file_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'score.py'
            path.write_text('import numpy as np\ndef compose(s,t,p): return {}\n')
            self.assertEqual(audit_score(path)['status'],'pass')
            path.write_text('import wave\n')
            with self.assertRaises(ValueError): audit_score(path)
            path.write_text('import numpy as np\nx=np.fromfile("reference.wav")\n')
            with self.assertRaises(ValueError): audit_score(path)

    def test_immutable_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json'
            immutable_json(path, {'value':1}); immutable_json(path, {'value':1})
            with self.assertRaises(ValueError): immutable_json(path, {'value':2})


class MediaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.project=Path(cls.temp.name)/'fixture'; cls.project.mkdir()
        cls.source=cls.project/'revisions/r001/source'
        (cls.source/'src').mkdir(parents=True); (cls.source/'audio').mkdir()
        cls.cfg=config(getattr(cls, 'frames', 48), getattr(cls, 'fps', None))
        (cls.source/'project.json').write_text(json.dumps(cls.cfg))
        (cls.source/'timeline.json').write_text(json.dumps({'shots':[{'id':'one','f0':0,'f1':cls.cfg['output']['frames']}],
            'events':[{'id':'tone','frame':0,'basis':'designed','source':'controlled test'}]}))
        (cls.source/'src/main.js').write_text('// Synthetic PNG test only\n')
        (cls.source/'audio/score.py').write_text('''def compose(synth, timeline, spec):
    n=spec['duration_samples']
    signal=.15*synth.oscillator(330,n)*synth.envelope(n,attack=.01,release=.1)
    track=synth.track()
    synth.place(track,signal,0,'tone')
    return {'tone':track}
''')
        files={str(p.relative_to(cls.source)):sha256(p) for p in cls.source.rglob('*') if p.is_file()}
        hashes=tool_hashes()
        fingerprint=hashlib.sha256(json.dumps({'files':files,'tools':hashes},sort_keys=True).encode()).hexdigest()
        cls.manifest={'revision':'r001','projectId':'fixture','files':files,'fingerprint':fingerprint,'toolHashes':hashes}
        (cls.source.parent/'manifest.json').write_text(json.dumps(cls.manifest))
        cls.output=cls.project/'out/r001'; (cls.output/'frames').mkdir(parents=True); (cls.output/'review').mkdir()
        frame_hashes={}
        for frame in range(cls.cfg['output']['frames']):
            path=cls.output/'frames'/f'{frame:06d}.png'
            Image.new('RGB',(64,64),(10+frame*3,60,190)).save(path)
            frame_hashes[str(frame)]=sha256(path)
        cls.render={'fingerprint':fingerprint,'frames':frame_hashes,'width':64,'height':64,
                    'fps':cls.cfg['output']['fps'],'determinism':'pass','toolHashes':hashes}
        (cls.output/'review/render.json').write_text(json.dumps(cls.render))
        cls.audio_report=synth_project(cls.project,'r001')
        cls.encode_report=preview(cls.project,'r001')

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_actual_synthesis_and_encoding(self):
        self.assertEqual(self.audio_report['status'],'pass',self.audio_report)
        self.assertEqual(self.encode_report['status'],'pass',self.encode_report)
        self.assertEqual(self.encode_report['decodedFrames'],self.cfg['output']['frames'])
        self.assertEqual(self.encode_report['audioSamples']['expected'],duration_samples(self.cfg))
        self.assertLessEqual(self.encode_report['encodedLoudness']['input_tp'],-1)
        self.assertEqual(self.encode_report['userAcceptance'],'unverified')
        self.assertEqual(len(self.encode_report['encodedCueAlignment']),1)
        self.assertEqual(self.encode_report['encodedCueAlignment'][0]['status'],'pass')

    def test_actual_color_transfer(self):
        result=subprocess.run(['ffmpeg','-v','error','-i',str(self.output/'preview.mp4'),'-frames:v','1','-pix_fmt','yuv420p','-f','rawvideo','pipe:1'],capture_output=True,check=True)
        rgb=np.array([10,60,190])/255
        linear=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
        bt709=np.where(linear<.018,linear*4.5,1.099*linear**.45-.099)
        expected=16+219*float(np.dot(bt709,[.2126,.7152,.0722]))
        self.assertLessEqual(abs(result.stdout[0]-expected),3)

    def test_normalized_master_has_exact_samples(self):
        pcm=read_pcm(self.output/'audio/master.wav',48000,2)
        self.assertEqual(pcm.shape,(duration_samples(self.cfg),2))
        self.assertLessEqual(abs(self.audio_report['loudness']['input_i']+14),1)

    def test_frame_tamper_detected(self):
        path=self.output/'frames/000003.png'; original=path.read_bytes()
        try:
            Image.new('RGB',(64,64),(255,0,0)).save(path)
            with self.assertRaises(ValueError): verify_frames(self.output,self.cfg,self.manifest)
        finally: path.write_bytes(original)

    def test_missing_qa_blocks_final(self):
        with self.assertRaises((ValueError,FileNotFoundError)): final(self.project,'r001')
        self.assertFalse((self.output/'final.mp4').exists())

    def test_fake_or_stale_qa_cannot_promote(self):
        checks=['technical','content','motion','audio_numeric','listening']
        qa=[{'check':name,'status':'pass','note':'asserted without evidence','evidence':[]} for name in checks]
        path=self.output/'review/qa.json'; path.write_text(json.dumps(qa))
        try:
            with self.assertRaises(ValueError): validate_qa(self.project,self.output,self.cfg,sha256(self.output/'preview.mp4'))
            for item in qa: item['evidence']=['out/r001/review/encode.json']
            path.write_text(json.dumps(qa))
            with self.assertRaises(ValueError): validate_qa(self.project,self.output,self.cfg,sha256(self.output/'preview.mp4'))
            with self.assertRaises(ValueError): validate_qa(self.project,self.output,self.cfg,'stale-hash')
        finally: path.unlink()

    def test_reference_check_cannot_be_omitted(self):
        cfg=dict(self.cfg,mode='match')
        path=self.output/'review/qa.json'; path.write_text('[]')
        try:
            with self.assertRaises(ValueError): validate_qa(self.project,self.output,cfg,sha256(self.output/'preview.mp4'))
        finally: path.unlink()


class RationalMediaIntegrationTests(MediaIntegrationTests):
    frames=60
    fps={'num':30000,'den':1001}


if __name__=='__main__': unittest.main(verbosity=2)
