"""Real headless Chromium tests; opt in via RM_PLAYWRIGHT_MODULE/RM_BROWSER."""
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
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import project
from _common import atomic_json, read_json
SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'

@unittest.skipUnless(os.environ.get('RM_BROWSER'), 'Set RM_BROWSER to run actual browser tests')
class RendererTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.w=Path(self.temp.name)/'ws';self.p=self.w/'projects/render-test'
        with contextlib.redirect_stdout(io.StringIO()):project.init(argparse.Namespace(workspace=str(self.w),id='render-test',mode='create',ref=None))
        self.c=read_json(self.p/'project.json');self.c.update(output={'width':64,'height':48,'fps':{'num':24,'den':1},'frames':4,'colorPolicy':'srgb-to-bt709-limited'},renderEntry='src/main.js',scoreEntry='audio/score.py')
        (self.p/'src').mkdir();(self.p/'audio').mkdir();(self.p/'audio/score.py').write_text('def compose(s,t,p):\n return {"s":s.track()}\n')
        for f in ('BRIEF.md','SPEC.md'):(self.p/f).write_text('Controlled engine test, no creative claim.')
        atomic_json(self.p/'timeline.json',{'shots':[{'id':'s','f0':0,'f1':4}],'events':[]})
    def prepare(self,body,prepare=''):
        atomic_json(self.p/'project.json',self.c)
        (self.p/'src/main.js').write_text('export async function prepare(e){'+prepare+'}\nexport function drawFrame(c,e){'+body+'}')
        with contextlib.redirect_stdout(io.StringIO()):project.snapshot(argparse.Namespace(project=str(self.p),revision='r001'))
    def run_render(self,*args):
        return subprocess.run(['node',str(SCRIPTS/'render.mjs'),'--project',str(self.p),'--revision','r001',*args],capture_output=True,text=True,timeout=70)
    def test_reset_clip_state_and_repeated_order(self):
        self.prepare("if(e.outputFrame===0){c.beginPath();c.rect(0,0,3,3);c.clip();c.globalAlpha=.5;c.filter='blur(1px)';}c.fillStyle='#ffffff';c.fillRect(0,0,64,48);")
        r=self.run_render('full','0','4');self.assertEqual(r.returncode,0,r.stderr)
        with Image.open(self.p/'out/r001/frames/000001.png') as im:self.assertEqual(im.getpixel((63,47)),(255,255,255,255))
        r=self.run_render('stills','3,0,3,1');self.assertEqual(r.returncode,0,r.stderr)
    def test_missing_font_is_failure(self):
        self.c['fonts']=[{'family':'ImpossibleFont','source':'local','name':'RM-Definitely-Missing-438987'}]
        self.prepare("c.fillRect(0,0,20,20)")
        r=self.run_render('full','0','4');self.assertNotEqual(r.returncode,0);self.assertFalse((self.p/'out/r001/review/render.json').exists())
    def test_missing_image_prepare_failure(self):
        self.prepare('',"const i=new Image();i.src='absent.png';await i.decode();")
        r=self.run_render('full','0','4');self.assertNotEqual(r.returncode,0)
    def test_stateful_module_failure(self):
        self.prepare("globalThis.counter=(globalThis.counter||0)+1;c.fillStyle=`rgb(${globalThis.counter},0,0)`;c.fillRect(0,0,64,48);")
        r=self.run_render('full','0','4');self.assertNotEqual(r.returncode,0);self.assertIn('non-deterministic',r.stderr)
    def test_random_draw_failure(self):
        self.prepare('c.fillRect(Math.random(),0,10,10);')
        r=self.run_render('full','0','4');self.assertNotEqual(r.returncode,0);self.assertIn('Unseeded',r.stderr)
    def test_fractional_out_of_bounds_and_no_fake_compare(self):
        self.prepare('c.fillRect(0,0,1,1);')
        for args in [('stills','0.5'),('full','0','5'),('compare','0'),('stills','4')]:
            with self.subTest(args=args):self.assertNotEqual(self.run_render(*args).returncode,0)
    def test_external_prepare_fetch_failure(self):
        self.prepare('',"await fetch('https://example.com/never-requested');")
        r=self.run_render('full','0','4');self.assertNotEqual(r.returncode,0)

if __name__=='__main__':unittest.main()
