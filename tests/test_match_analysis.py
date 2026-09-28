import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import match
import project
from _common import sha256,read_json,load_revision
from analysis_fixture import populate
SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'


class MeasurementTests(unittest.TestCase):
    def test_local_peaks_not_threshold_runs(self):
        r=match.local_peaks([0,4,20,20,19,0,1,2,0],8,4,2,3)
        self.assertEqual([x['frame'] for x in r],[2])
    def test_flash_peak_is_only_candidate(self):
        r=match.local_peaks([0,30,30,0],8,2,2,3)
        self.assertEqual(len(r),1);self.assertNotIn('hard_cut',r[0])
    def test_low_contrast_peak_and_no_peak(self):
        self.assertEqual(match.local_peaks([0,0,0,0]),[])
        self.assertEqual(match.local_peaks([0,3,0],2,1,1,1)[0]['frame'],1)
    def test_actual_ink_bbox_and_missing(self):
        a=np.zeros((30,40,3),dtype=np.uint8);a[7:13,12:20]=255
        self.assertEqual(match.ink_mask(a,[10,5,20,20],[0,0,0],20),{'x':12,'y':7,'w':8,'h':6,'pixels':48})
        self.assertIsNone(match.ink_mask(a,[0,0,5,5],[0,0,0],20))
    def test_ink_roi_out_of_bounds(self):
        with self.assertRaises(ValueError):match.ink_mask(np.zeros((10,10,3)),[5,5,9,9],[0,0,0],2)
    def test_template_translation_and_luminance_invariance(self):
        t=np.random.default_rng(4).integers(0,100,(7,9)).astype(float);a=np.zeros((35,50));a[19:26,31:40]=t*1.8+30
        r=match.template_search(a,t,.99,.02);self.assertEqual(r['offset'],[31,19]);self.assertAlmostEqual(r['score'],1,places=8)
    def test_template_ambiguity_and_occlusion_preserve_null(self):
        t=np.random.default_rng(7).integers(0,255,(5,7));a=np.zeros((20,40));a[3:8,2:9]=t;a[3:8,25:32]=t
        self.assertIsNone(match.template_search(a,t,.85,.02)['offset'])
        self.assertIsNone(match.template_search(np.zeros((20,40)),t)['offset'])
    def test_flat_template_rejected(self):
        with self.assertRaisesRegex(ValueError,'Flat'):match.template_search(np.ones((20,20)),np.ones((3,3)))
    def test_camera_fit_reports_residuals(self):
        x=np.array([[0,0],[10,0],[0,10]],float);y=x*1.4+[3,-2]
        fit=match.similarity_fit(x,y);self.assertAlmostEqual(fit['scale'],1.4);self.assertLess(fit['maxResidualPx'],1e-9)
        y[2]+=[8,1];self.assertGreater(match.similarity_fit(x,y)['maxResidualPx'],1)
    def test_degenerate_camera_rejected(self):
        with self.assertRaises(ValueError):match.similarity_fit([[0,0],[0,0]],[[1,1],[1,1]])


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.ws=self.root/'workspace'
        refdir=self.root/'ref';refdir.mkdir()
        for f in range(4):
            a=np.zeros((64,80,3),dtype=np.uint8);a[38:58,20+f:40+f]=255;Image.fromarray(a).save(refdir/f'{f:06d}.png')
        self.ref=refdir/'fixture.mkv'
        subprocess.run(['ffmpeg','-v','error','-framerate','24','-i',str(refdir/'%06d.png'),'-frames:v','4','-c:v','ffv1','-pix_fmt','bgr0',str(self.ref)],check=True)
        with contextlib.redirect_stdout(io.StringIO()):project.init(argparse.Namespace(workspace=self.ws,id='fixture',mode='match',ref=self.ref))
        self.p=self.ws/'projects/fixture';self.c=read_json(self.p/'project.json');self.c.update(output={'width':80,'height':64,'fps':{'num':24,'den':1},'frames':4,'colorPolicy':'srgb-to-bt709-limited'},renderEntry='src/main.js',scoreEntry='audio/score.py')
        (self.p/'project.json').write_text(json.dumps(self.c));(self.p/'src').mkdir();(self.p/'audio').mkdir()
        (self.p/'src/main.js').write_text("export async function prepare(){}\nexport function drawFrame(c,e){c.fillRect(0,0,80,64)}")
        (self.p/'audio/score.py').write_text('def compose(s,t,p):\n return {"silence":s.track()}\n')
        (self.p/'timeline.json').write_text(json.dumps({'shots':[{'id':'travel','f0':0,'f1':4}],'events':[]}))
        for name in ('BRIEF.md','SPEC.md'):(self.p/name).write_text('Explicit engineering fixture only.')
        self.d=populate(self.p)
    def write(self): (self.p/'analysis/match.json').write_text(json.dumps(self.d))
    def test_ready_actual_fixture_and_frozen_analysis(self):
        self.assertEqual(match.analysis_gate(self.p)['status'],'ready')
        with contextlib.redirect_stdout(io.StringIO()):project.snapshot(argparse.Namespace(project=self.p,revision='r001'))
        self.assertTrue((self.p/'revisions/r001/source/analysis/match.json').exists())
        self.assertEqual(load_revision(self.p,'r001',True)[3]['toolVersion'],'0.2.0')
    def test_missing_analysis_blocks_production_snapshot(self):
        (self.p/'analysis/match.json').unlink()
        with self.assertRaisesRegex(ValueError,'Match analysis gate'):project.snapshot(argparse.Namespace(project=self.p,revision='r001'))
        self.assertFalse((self.p/'revisions/r001').exists())
    def test_unknown_is_probe_only_and_cannot_be_renamed_measured(self):
        self.d['cursor']={'state':'unknown','reason':'Cannot isolate cursor','items':[],'evidence':[]};self.write()
        self.assertFalse(match.analysis_gate(self.p)['allowed']);self.assertTrue(match.analysis_gate(self.p,stage='probe')['allowed'])
        with contextlib.redirect_stdout(io.StringIO()):project.snapshot(argparse.Namespace(project=self.p,revision='p001',probe_frames='0,3'))
        frozen=read_json(self.p/'revisions/p001/source/project.json');self.assertEqual(frozen['probeFrames'],[0,3]);self.assertEqual(frozen['renderPurpose'],'analysis-probe')
        self.d['cursor']['state']='measured';self.write();self.assertEqual(match.analysis_gate(self.p,stage='probe')['status'],'blocked')
    def test_changed_evidence_hash_blocks(self):
        (self.p/'analysis/evidence/white.json').write_text('{}');self.assertEqual(match.analysis_gate(self.p)['status'],'blocked')
    def test_foreign_reference_and_bad_index_block(self):
        self.d['referenceSha256']='0'*64;self.write();self.assertEqual(match.analysis_gate(self.p)['status'],'blocked')
    def test_missing_component_frames_cannot_pass(self):
        f=self.p/'analysis/evidence/rectangle.json';r=read_json(f);r['observations'][2]['bbox']=None;f.write_text(json.dumps(r))
        for section in self.d.values():
            if isinstance(section,dict):
                for link in section.get('evidence',[]):
                    if link['path']=='analysis/evidence/rectangle.json':link['sha256']=sha256(f)
        self.write();self.assertIn('component:rectangle:coverage',match.analysis_gate(self.p)['unknown'])
    def test_template_window_size_is_not_measured_object_size(self):
        f=self.p/'analysis/evidence/rectangle.json';r=read_json(f);r['measuredProperties']=['x','y'];f.write_text(json.dumps(r))
        for section in self.d.values():
            if isinstance(section,dict):
                for link in section.get('evidence',[]):
                    if link['path']=='analysis/evidence/rectangle.json':link['sha256']=sha256(f)
        self.write();self.assertFalse(match.analysis_gate(self.p)['allowed'])
    def test_not_applicable_is_not_blank_bypass(self):
        self.d['colors']={'state':'not_applicable','reason':'skip','items':[],'evidence':self.d['audio']['evidence']};self.write();self.assertEqual(match.analysis_gate(self.p)['status'],'blocked')
    def test_spec_writes_unknowns_and_never_overwrites(self):
        args=argparse.Namespace(project=self.p,out='analysis/SPEC.md')
        with contextlib.redirect_stdout(io.StringIO()):match.spec(args)
        with self.assertRaisesRegex(ValueError,'overwrite'):match.spec(args)
    def test_cut_candidate_needs_observed_classification(self):
        f=self.p/'analysis/evidence/cuts.json';r=read_json(f);r['candidates']=[{'frame':2,'mad':30}];f.write_text(json.dumps(r))
        self.d['cuts']['evidence'][0]['sha256']=sha256(f);self.write();self.assertIn('cut:2',match.analysis_gate(self.p)['unknown'])
    def test_index_missing_hash_cannot_bind_missing_observation_hash(self):
        f=self.p/'analysis/index.json';r=read_json(f);del r['decode']['frameHashes']['2'];f.write_text(json.dumps(r));self.d['referenceIndex']['sha256']=sha256(f);self.write()
        self.assertEqual(match.analysis_gate(self.p)['status'],'blocked')
    def test_timeline_mismatch_and_invalid_event_block(self):
        self.d['shots']['items'][0]['id']='different';self.d['events']=[{'id':'bad','frame':8,'basis':'declared'}];self.write()
        r=match.analysis_gate(self.p);self.assertEqual(r['status'],'blocked');self.assertTrue(any('timeline' in x for x in r['issues']))
    def test_old_restore_fails_before_working_copy_mutation(self):
        with contextlib.redirect_stdout(io.StringIO()):project.snapshot(argparse.Namespace(project=self.p,revision='r001'))
        before=(self.p/'src/main.js').read_text();source=self.p/'revisions/r001/source'
        with patch.object(project,'load_revision',return_value=(source,self.c,{},{})),patch.object(project,'validate_project',side_effect=ValueError('migration needed')):
            with self.assertRaisesRegex(ValueError,'migration'):project.restore(argparse.Namespace(project=self.p,from_revision='r001',revision='r002'))
        self.assertEqual((self.p/'src/main.js').read_text(),before);self.assertFalse((self.p/'history/working-before-restore-r002').exists())
    def test_discrete_locks_need_actual_artifact_bound_events(self):
        self.d['events']=[{'id':'arrival','frame':2,'basis':'controlled fixture'}];self.write();out=self.root/'out';(out/'review').mkdir(parents=True)
        self.assertEqual(match.locked_event_check(self.p,out,'a')['status'],'unverified')
        evidence={'artifactSha256':'a','reviewer':'test fixture','method':'controlled event observation','observations':['Fixture only'],'events':[{'id':'arrival','actualFrame':3}]}
        (out/'review/events.json').write_text(json.dumps(evidence));self.assertEqual(match.locked_event_check(self.p,out,'a')['status'],'fail')
        evidence['events'][0]['actualFrame']=2;(out/'review/events.json').write_text(json.dumps(evidence));self.assertEqual(match.locked_event_check(self.p,out,'a')['status'],'pass')
        self.assertEqual(match.locked_event_check(self.p,out,'different')['status'],'unverified')
    def test_visual_coverage_is_not_inferred_from_frame_extraction(self):
        self.d['review']['ranges']=[[0,1]];self.write();self.assertIn('visual review coverage',match.analysis_gate(self.p)['unknown'])
    def test_missing_ink_and_inferred_lock_do_not_pass(self):
        f=self.p/'analysis/evidence/ink.json';match.save_new(f,{'kind':'ink','referenceSha256':self.c['reference']['sha256'],'frame':0,'imageSha256':read_json(self.p/'analysis/index.json')['decode']['frameHashes']['0'],'bbox':None})
        self.d['typography']={'state':'measured','reason':'Controlled negative measurement','items':[{'measurement':'analysis/evidence/ink.json'}],'evidence':[{'path':'analysis/evidence/ink.json','sha256':sha256(f)}]}
        self.d['events']=[{'id':'candidate','frame':2,'basis':'inferred'}];self.write();r=match.analysis_gate(self.p)
        self.assertFalse(r['allowed']);self.assertIn('typography:missing ink',r['unknown']);self.assertIn('event:candidate',r['unknown'])


@unittest.skipUnless(os.environ.get('RM_BROWSER'),'Set RM_BROWSER for real Chromium match workflow')
class MatchWorkflowTests(unittest.TestCase):
    setUp=GateTests.setUp
    write=GateTests.write
    def render(self,revision,*args):
        return subprocess.run(['node',str(SCRIPTS/'render.mjs'),'--project',str(self.p),'--revision',revision,*args],capture_output=True,text=True,timeout=70)
    def test_probe_limits_render_and_encode(self):
        import encode
        self.d['cursor']={'state':'unknown','reason':'Unresolved fixture cursor','items':[],'evidence':[]};self.write()
        with contextlib.redirect_stdout(io.StringIO()):project.snapshot(argparse.Namespace(project=self.p,revision='p001',probe_frames='0,3'))
        good=self.render('p001','stills','0,3');self.assertEqual(good.returncode,0,good.stderr)
        for args in [('full','0','4'),('stills','1')]:
            r=self.render('p001',*args);self.assertNotEqual(r.returncode,0);self.assertIn('probe',r.stderr.lower())
        with self.assertRaisesRegex(ValueError,'probe'):encode.preview(self.p,'p001')
    def test_real_match_failure_correction_and_final_gate(self):
        import audio,encode
        self.c['audio']['allowSilence']=True;(self.p/'project.json').write_text(json.dumps(self.c))
        for revision,offset,expected in [('r001',2,'fail'),('r002',0,'pass')]:
            (self.p/'src/main.js').write_text('export async function prepare(){}\nexport function drawFrame(c,e){c.fillStyle="#000";c.fillRect(0,0,80,64);c.fillStyle="#fff";c.fillRect('+str(20+offset)+'+e.outputFrame,38,20,20)}')
            with contextlib.redirect_stdout(io.StringIO()):project.snapshot(argparse.Namespace(project=self.p,revision=revision))
            r=self.render(revision,'full','0','4');self.assertEqual(r.returncode,0,r.stderr)
            for script,args in [('audio.py',['synth']),('encode.py',['--preview'])]:
                r=subprocess.run([sys.executable,str(SCRIPTS/script),'--project',str(self.p),'--revision',revision,*args],capture_output=True,text=True,timeout=70);self.assertEqual(r.returncode,0,r.stderr)
            with contextlib.redirect_stdout(io.StringIO()):
                if expected=='fail':
                    with self.assertRaisesRegex(ValueError,'did not pass'):match.verify(argparse.Namespace(project=self.p,revision=revision))
                else:match.verify(argparse.Namespace(project=self.p,revision=revision))
            self.assertEqual(read_json(self.p/f'out/{revision}/review/match-verification.json')['status'],expected)
        with self.assertRaises((ValueError,FileNotFoundError)):encode.final(self.p,'r002')
        self.assertFalse((self.p/'out/r002/final.mp4').exists())
        report=self.p/'out/r002/review/match-verification.json';r=read_json(report);r['artifactSha256']='0'*64;report.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'artifact-bound'):match.require_match_verification(self.p,'r002',self.c,load_revision(self.p,'r002')[3],'a')


if __name__=='__main__':unittest.main()
