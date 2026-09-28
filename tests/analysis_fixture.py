"""Authored engineering fixture analysis from actual decoded pixels. Never production creative memory."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import analyze
import match
import measure
from _common import read_json,sha256


def populate(project):
    p=Path(project);c=read_json(p/'project.json')
    with contextlib.redirect_stdout(io.StringIO()):analyze.analyze(p)
    index=read_json(p/'ref/index.json');root=p/'analysis';root.mkdir(exist_ok=True);ev=root/'evidence';ev.mkdir(exist_ok=True)
    shutil.copyfile(p/'ref/index.json',root/'index.json')
    def link(path):return {'path':str(path.relative_to(p)),'sha256':sha256(path)}
    with contextlib.redirect_stdout(io.StringIO()):
        match.cuts(argparse.Namespace(project=p,out='analysis/evidence/cuts.json',minimum=8,prominence=4,radius=3,distance=3))
        match.color(argparse.Namespace(project=p,out='analysis/evidence/black.json',frame=0,roi='0,0,4,4'))
        match.color(argparse.Namespace(project=p,out='analysis/evidence/white.json',frame=0,roi='22,40,4,4'))
    detector={'roi':[0,0,index['width'],index['height']],'color':[255,255,255],'tolerance':8,'minPixels':4,'component':'largest'}
    rows=measure.observe(p/'ref/frames',[0,index['frames']],detector,(index['width'],index['height']))
    track={'kind':'track','referenceSha256':index['sourceSha256'],'object':'rectangle','method':'Actual native RGB threshold/four-connected components','measuredProperties':['x','y','w','h'],'detector':detector,'observations':rows,'interpolated':False,'range':[0,index['frames']]}
    match.save_new(ev/'rectangle.json',track)
    review={'kind':'review','referenceSha256':index['sourceSha256'],'reviewer':'engineering fixture program, no real-reference perceptual claim','method':'Independent decoded pixel components across all frames plus fixture source provenance','observations':['All reference frames have a detected white component. No reference sound stream. No authored text/cursor/brand swaps in this controlled fixture.']}
    match.save_new(ev/'review.json',review)
    def section(state,reason,items=None,evidence=None):return {'state':state,'reason':reason,'items':items or [],'evidence':[link(x) for x in (evidence or [ev/'review.json'])]}
    n=index['frames'];d={'schemaVersion':1,'referenceSha256':index['sourceSha256'],'referenceIndex':link(root/'index.json'),'output':c['output'],
      'cuts':section('reviewed','Synthetic no-cut fixture; programmatic pixel inspection, not a human review.',[],[ev/'cuts.json',ev/'review.json']),
      'shots':section('reviewed','One continuous authored fixture',[{'id':'travel','f0':0,'f1':n,'content':'White rectangle on black','entrance':'Already visible at frame zero','exit':'Capture ends','details':'Independent Pillow reference and project Canvas response; no shot template reused.'}]),
      'components':section('reviewed','Detected fixture object',[{'id':'rectangle','kind':'shape','critical':True,'visibleRanges':[[0,n]],'properties':['x','y','w','h'],'tracks':['analysis/evidence/rectangle.json']}],[ev/'review.json',ev/'rectangle.json']),
      'colors':section('measured','Actual decoded ROI medians',[{'id':'background','role':'background','measurement':'analysis/evidence/black.json'},{'id':'rectangle-fill','role':'foreground','measurement':'analysis/evidence/white.json'}],[ev/'black.json',ev/'white.json']),
      'motion':section('measured','Measured all fixture frames',[{'component':'rectangle','measurement':'analysis/evidence/rectangle.json'}],[ev/'rectangle.json']),
      'review':{'reviewer':review['reviewer'],'method':review['method'],'ranges':[[0,n]],'observations':review['observations']},'events':[]}
    for k in ('text','typography','cursor','camera','audio','replacements'):d[k]=section('not_applicable','Not present in the independently authored controlled fixture; not generalized to real video.')
    match.save_new(root/'match.json',d)
    return d
