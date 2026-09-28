#!/usr/bin/env python3
"""Independent Pillow reference and Canvas reconstruction: engineering-only fixture."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from PIL import Image, ImageDraw


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--workspace',required=True)
    args=parser.parse_args(); w=Path(args.workspace).resolve(); scripts=Path(__file__).resolve().parents[1]/'scripts'
    fixture=w/'fixtures'/'independent-reference'; fixture.mkdir(parents=True,exist_ok=False)
    for frame in range(48):
        im=Image.new('RGB',(160,96),(0,0,0)); draw=ImageDraw.Draw(im)
        x=20+frame; draw.rectangle((x,38,x+19,57),fill='white')
        im.save(fixture/f'{frame:06d}.png')
    ref=fixture/'engineering-reference.mkv'
    subprocess.run(['ffmpeg','-v','error','-framerate','24','-i',str(fixture/'%06d.png'),'-frames:v','48','-c:v','ffv1','-pix_fmt','bgr0',str(ref)],check=True)
    (fixture/'PROVENANCE.md').write_text('This reference is independently generated with Pillow rectangle rasterization. It is an engineering fixture, not a user reference or evidence of real-world reverse engineering. Source: tests/build_match_fixture.py. No audio. Canvas output uses separate project-authored code.\n')
    subprocess.run([sys.executable,str(scripts/'project.py'),'init','--workspace',str(w),'--id','match-fixture','--mode','match','--ref',str(ref)],check=True)
    p=w/'projects/match-fixture'; c=json.loads((p/'project.json').read_text())
    c.update(output={'width':160,'height':96,'fps':{'num':24,'den':1},'frames':48,'colorPolicy':'srgb-to-bt709-limited'},renderEntry='src/main.js',scoreEntry='audio/score.py',locks=[{'id':'rectangle-geometry','basis':'measured','scope':'all frames'}])
    c['audio'].update(allowSilence=True,design={'intent':'Silent fixture. Silence is explicit; no fabricated source audio.'})
    (p/'project.json').write_text(json.dumps(c,indent=2)+'\n')
    (p/'src').mkdir(); (p/'audio').mkdir()
    (p/'src/main.js').write_text("export async function prepare(e) {}\nexport function drawFrame(c,e){ c.fillStyle='#000';c.fillRect(0,0,e.width,e.height);c.fillStyle='#fff';c.fillRect(23+e.sampleFrame,38,20,20); }\n")
    (p/'audio/score.py').write_text('def compose(synth,timeline,spec):\n    return {"explicit_silence":synth.track()}\n')
    (p/'timeline.json').write_text(json.dumps({'shots':[{'id':'travel','f0':0,'f1':48}],'events':[],'transitions':[]},indent=2)+'\n')
    (p/'BRIEF.md').write_text('# Controlled match fixture\nReconstruct the independent 160×96, 24fps, 48-frame engineering reference. No actual user reference exists. No creative or reverse-engineering capability claim. Original silent source stays silent.\n')
    (p/'SPEC.md').write_text('# Measured reconstruction experiment\nThe reference contains one white 20×20 pixel rectangle moving horizontally on black. Use full-frame white threshold measurement. Initial reconstruction deliberately starts three pixels too far right to test rejection and correction. All 48 visible frames must be measured. No semantic inference or user approval is tested.\n')
    (p/'REPORT.md').write_text('Prepared independent reference and intentionally inaccurate initial reconstruction. Rendering, measurement, correction and comparison remain pending.\n')
    from analysis_fixture import populate
    populate(p)
    print(p)

if __name__=='__main__': main()
