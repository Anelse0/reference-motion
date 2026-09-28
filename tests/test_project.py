import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from _common import atomic_json, duration_samples, load_revision, safe_path, sha256, validate_project
import project as lifecycle

class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.w=Path(self.temp.name)/'ws';self.p=self.w/'projects/a'
        lifecycle.init(argparse.Namespace(workspace=str(self.w),id='a',mode='create',ref=None))
    def authored(self):
        c=json.loads((self.p/'project.json').read_text())
        c.update(output={'width':160,'height':96,'fps':{'num':30000,'den':1001},'frames':60,'colorPolicy':'srgb-to-bt709-limited'},renderEntry='src/main.js',scoreEntry='audio/score.py')
        atomic_json(self.p/'project.json',c)
        (self.p/'src').mkdir();(self.p/'audio').mkdir()
        (self.p/'src/main.js').write_text('export async function prepare(){};export function drawFrame(c,e){}')
        (self.p/'audio/score.py').write_text('def compose(s,t,p):\n return {"silence":s.track()}\n')
        for name in ('BRIEF.md','SPEC.md'): (self.p/name).write_text('Explicit controlled test only')
        atomic_json(self.p/'timeline.json',{'shots':[{'id':'s','f0':0,'f1':60}],'events':[]})
        return c
    def snap(self,r='r001'): lifecycle.snapshot(argparse.Namespace(project=str(self.p),revision=r))
    def pref(self,id='p',scope=None,status='confirmed',derived=None):
        source=self.w/'instruction.txt';source.write_text('SYNTHETIC TEST FIXTURE: In this test project only use neutral framing. Not a real user preference.')
        return {'id':id,'category':'aesthetic','rule':'fixture only','scope':scope or {'type':'project','id':'a'},'appliesWhen':'controlled fixture','status':status,'sources':[{'path':'instruction.txt','sha256':sha256(source),'explicitConfirmation':True}],'updatedAt':'2026-09-28','supersedes':None,'derivedFrom':derived or []}
    def test_init_empty_without_template(self):
        c,t=validate_project(self.p,'init');self.assertIsNone(t);self.assertIsNone(c['reference'])
        self.assertFalse((self.p/'src').exists());self.assertFalse((self.p/'SPEC.md').exists())
        self.assertEqual(json.loads((self.w/'memory/preferences.json').read_text()),[])
    def test_init_no_overwrite(self):
        h=sha256(self.p/'project.json')
        with self.assertRaises(FileExistsError):lifecycle.init(argparse.Namespace(workspace=str(self.w),id='a',mode='create',ref=None))
        self.assertEqual(h,sha256(self.p/'project.json'))
    def test_match_missing_ref(self):
        with self.assertRaises(ValueError):lifecycle.init(argparse.Namespace(workspace=str(self.w),id='b',mode='match',ref=None))
    def test_render_rejects_pending(self):
        with self.assertRaises((ValueError,TypeError)):validate_project(self.p)
    def test_rational_audio_duration(self):
        c=self.authored();self.assertEqual(duration_samples(c),96096)
    def test_half_open_gap_and_duplicate_anchors(self):
        self.authored();t={'shots':[{'id':'s','f0':1,'f1':60}],'events':[]};atomic_json(self.p/'timeline.json',t)
        with self.assertRaises(ValueError):validate_project(self.p)
        t['shots'][0]['f0']=0;t['events']=[{'id':'x','frame':1,'seconds':1,'basis':'designed','source':'test'}];atomic_json(self.p/'timeline.json',t)
        with self.assertRaises(ValueError):validate_project(self.p)
    def test_snapshot_frozen_and_modified_current_independent(self):
        self.authored();self.snap();(self.p/'src/main.js').write_text('changed current source')
        src,c,t,m=load_revision(self.p,'r001');self.assertNotEqual((src/'src/main.js').read_text(),'changed current source')
        with self.assertRaises(ValueError):self.snap()
        (src/'src/main.js').write_text('tamper')
        with self.assertRaises(ValueError):load_revision(self.p,'r001')
    def test_path_escape_and_symlink(self):
        outside=Path(self.temp.name)/'outside';outside.write_text('private')
        (self.p/'link').symlink_to(outside)
        for rel in ['../../../outside','link']:
            with self.assertRaises(ValueError):safe_path(self.p,rel)
    def test_compare_swap_conflict(self):
        p=self.w/'memory/preferences.json';h=sha256(p);atomic_json(p,[{'changed':True}],expected_hash=h)
        with self.assertRaises(ValueError):atomic_json(p,[],expected_hash=h)
    def test_scope_override_candidate_and_match_lock(self):
        c=self.authored();r=self.pref();atomic_json(self.w/'memory/preferences.json',[r])
        self.assertEqual(len(lifecycle.active_memory(self.w,c)['active']),1)
        c['id']='b';self.assertFalse(lifecycle.active_memory(self.w,c)['active']);c['id']='a'
        c['memoryOverrides']=['p'];self.assertFalse(lifecycle.active_memory(self.w,c)['active']);del c['memoryOverrides']
        c['mode']='match';self.assertFalse(lifecycle.active_memory(self.w,c)['active']);c['mode']='create'
        r['status']='candidate';atomic_json(self.w/'memory/preferences.json',[r]);self.assertFalse(lifecycle.active_memory(self.w,c)['active'])
    def test_confirmed_requires_source(self):
        r=self.pref();r['sources'][0].pop('explicitConfirmation')
        with self.assertRaises(ValueError):lifecycle.validate_memory(self.w,'preferences',[r])
    def test_forget_restore_no_revival(self):
        c=self.authored();r=self.pref();atomic_json(self.w/'memory/preferences.json',[r]);self.snap()
        # Exercise CLI argument dispatch without deleting source feedback or historical film.
        old=sys.argv;sys.argv=['project.py','forget','--workspace',str(self.w),'--id','p']
        try:lifecycle.main()
        finally:sys.argv=old
        lifecycle.restore(argparse.Namespace(project=str(self.p),from_revision='r001',revision='r002'))
        self.assertFalse(lifecycle.active_memory(self.w,c)['active'])
        self.assertTrue((self.p/'history/working-before-restore-r002/src/main.js').exists())
        lifecycle.validate_memory(self.w,'preferences',json.loads((self.w/'memory/preferences.json').read_text()))
    def test_delete_derived_and_reactivation_blocked(self):
        c=self.authored();a=self.pref();b=self.pref('derived',derived=['p']);atomic_json(self.w/'memory/preferences.json',[a,b])
        old=sys.argv;sys.argv=['project.py','forget','--workspace',str(self.w),'--id','p','--delete']
        try:lifecycle.main()
        finally:sys.argv=old
        self.assertEqual(json.loads((self.w/'memory/preferences.json').read_text()),[])
        with self.assertRaises(ValueError):lifecycle.validate_memory(self.w,'preferences',[a])
    def test_cross_workspace_denied(self):
        c=self.authored();c['learning']['workspace']='../../..'
        with self.assertRaises(ValueError):lifecycle.workspace_for(self.p,c)
    def test_liking_not_capability_and_stale_code(self):
        self.authored();p=self.w/'evidence.json';p.write_text('{"status":"pass"}')
        c={'id':'cap','task':'test','method':'controlled','conditions':'tiny','status':'verified_in_scope','evidence':[{'path':'evidence.json','sha256':sha256(p),'kind':'user_like'}],'limits':'fixture only','environment':{'toolHashes':{}},'codeRef':{'path':'projects/a/src/main.js','sha256':sha256(self.p/'src/main.js')},'lastTested':'2026-09-28','checkMethod':'numeric'}
        with self.assertRaises(ValueError):lifecycle.validate_memory(self.w,'capabilities',[c])
        c['evidence'][0]['kind']='test';atomic_json(self.w/'memory/capabilities.json',[c])
        self.assertFalse(lifecycle.active_memory(self.w,json.loads((self.p/'project.json').read_text()))['active'])
    def test_feedback_missing_reviewed_revision_and_false_acceptance(self):
        self.authored();self.snap()
        r={'id':'f','projectId':'a','reviewedRevision':'r001','quote':'fixture','source':{'kind':'test_fixture'},'scope':{'type':'project','id':'a'},'userOutcome':'unknown'}
        with self.assertRaises(ValueError):lifecycle.validate_feedback(self.p,[r])
        (self.p/'out/r001').mkdir(parents=True);(self.p/'out/r001/preview.mp4').write_bytes(b'test-only-placeholder')
        r['userOutcome']='accepted'
        with self.assertRaises(ValueError):lifecycle.validate_feedback(self.p,[r])

if __name__=='__main__':unittest.main()
