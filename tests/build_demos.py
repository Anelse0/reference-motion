#!/usr/bin/env python3
"""Author two isolated engineering/demo projects. Never installed as creative presets.

This script prepares source only. The production CLI is then run separately so
actual render/audio/encode evidence cannot be confused with authored intentions.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


PACKET_JS = r'''export async function prepare(env) {
  if (env.width !== 640 || env.height !== 360) throw new Error("This authored composition is 640 by 360");
}
const clamp = x => Math.max(0, Math.min(1, x));
const smooth = x => { x = clamp(x); return x*x*(3-2*x); };
const event = (e, id) => e.timeline.events.find(x => x.id === id).frame;
function type(c, text, x, y, size, color, align='left') {
  c.fillStyle=color; c.font=`400 ${size}px Arial`; c.textAlign=align; c.textBaseline='alphabetic'; c.fillText(text,x,y);
}
function packet(c,x,y,color,angle=0) {
  c.save(); c.translate(x,y); c.rotate(angle); c.fillStyle=color; c.fillRect(-15,-4,30,8); c.restore();
}
export function drawFrame(c,e) {
  const f=e.sampleFrame, W=e.width, H=e.height, ink='#17384A';
  const colors=['#D87636','#D8AD46','#5C9ABC','#D87636','#D8AD46','#5C9ABC'];
  c.fillStyle='#FAF7F0'; c.fillRect(0,0,W,H);
  type(c,'ONE MESSAGE. MANY PACKETS.',32,52,27,ink);
  type(c,'A schematic view of packet routing',33,77,14,'#61727A');
  const route=event(e,'routing-start'), reassembled=event(e,'reassembled');
  const movement=smooth((f-route)/8), finished=smooth((f-reassembled)/7);
  c.strokeStyle='#CBD2D0'; c.lineWidth=1.2;
  for(let k=0;k<3;k++) {
    const y=173, bend=(k-1)*74;
    c.beginPath(); c.moveTo(126,y); c.bezierCurveTo(236,y+bend,390,y+bend,514,y); c.stroke();
  }
  c.fillStyle='#FAF7F0'; c.strokeStyle='#AAB9BD';
  for(const [x,y] of [[260,118],[365,228],[310,173]]) {c.beginPath(); c.arc(x,y,9,0,Math.PI*2);c.fill();c.stroke();}
  c.strokeStyle=ink; c.lineWidth=1.4;
  c.strokeRect(57,118,79,123); c.strokeRect(503,118,79,123);
  type(c,'SENDER',96,264,12,ink,'center'); type(c,'RECEIVER',542,264,12,ink,'center');
  for(let i=0;i<6;i++) {
    const f0=route+i*2, f1=reassembled-2-(5-i)*2;
    const q=smooth((f-f0)/(f1-f0));
    const x=96+446*q, y0=140+i*14, y1=y0;
    const bend=(i%3-1)*65;
    const y=y0+(y1-y0)*q+Math.sin(Math.PI*q)*bend;
    const angle=(q>0&&q<1)?Math.atan2(Math.PI*bend*Math.cos(Math.PI*q),446):0;
    packet(c,x,y,colors[i],angle);
  }
  c.globalAlpha=1-finished;
  type(c,f<route?'One message is divided into parts.':'The parts travel across different routes.',320,310,19,ink,'center');
  c.globalAlpha=finished;
  type(c,'REASSEMBLE THE MESSAGE',320,310,21,ink,'center');
  c.globalAlpha=1;
  // These marks describe this illustration only; they are not a network guarantee.
  type(c,'ILLUSTRATION / timing and paths simplified',32,343,11,'#7B8588');
}'''

PACKET_SCORE = r'''import numpy as np

def compose(synth, timeline, spec):
    sr=spec["sample_rate"]
    fps=spec["fps"]["num"]/spec["fps"]["den"]
    events={e["id"]:e for e in timeline["events"]}
    route=synth.track()
    arrival=synth.track()
    for event_id, frequency, pan in [("routing-start",520,-0.65),("routing-middle",710,0.0)]:
        n=round(sr*0.21)
        t=np.arange(n)/sr
        noise=synth.lowpass(synth.noise(n,event_id),1800)
        signal=(0.09*synth.oscillator(frequency,n)+0.028*noise)*np.exp(-t*15)
        signal*=synth.envelope(n,attack=.003,release=.04)
        synth.place(route,synth.pan(signal,pan),round(events[event_id]["frame"]*sr/fps),event_id)
    n=round(sr*.72)
    t=np.arange(n)/sr
    chord=sum(synth.oscillator(hz,n) for hz in (330,440,660))*.035
    chord*=np.exp(-t*4)*synth.envelope(n,attack=.008,release=.22)
    synth.place(arrival,synth.pan(chord,.30),round(events["reassembled"]["frame"]*sr/fps),"reassembled")
    return {"routing_transients":route,"reassembly_resonance":arrival},synth.events
'''

WAVE_JS = r'''export async function prepare(env) {
  if (env.width !== 480 || env.height !== 480) throw new Error("This authored composition is 480 square");
}
const smooth=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x);};
function label(c,s,x,y,size,color,align='left') {
  c.font=`400 ${size}px Arial`;c.fillStyle=color;c.textAlign=align;c.textBaseline='alphabetic';c.fillText(s,x,y);
}
function graph(c,y,height,color,fn) {
  c.strokeStyle='#344C47';c.lineWidth=1;c.beginPath();c.moveTo(31,y);c.lineTo(449,y);c.stroke();
  c.strokeStyle=color;c.lineWidth=2.6;c.beginPath();
  for(let i=0;i<=418;i++){const x=31+i, yy=y-fn(i/418)*height;if(i===0)c.moveTo(x,yy);else c.lineTo(x,yy);}
  c.stroke();
}
export function drawFrame(c,e) {
  const t=e.timeSeconds, second=e.timeline.events.find(x=>x.id==='second-tone').frame*e.fps.den/e.fps.num;
  const b=smooth((t-second)/.18), phaseTime=t*.05;
  const a=x=>Math.sin(2*Math.PI*220*(phaseTime+x*.02));
  const v=x=>Math.sin(2*Math.PI*224*(phaseTime+x*.02))*b;
  c.fillStyle='#112D28';c.fillRect(0,0,480,480);
  label(c,'WHEN WAVES MEET',31,46,29,'#F1EAD8');
  label(c,'Two close pitches create a pulsing sum.',32,72,16,'#B1C0AD');
  label(c,'220 Hz',32,113,14,'#E6BE73');
  graph(c,151,24,'#E6BE73',a);
  c.globalAlpha=.26+.74*b;
  label(c,'224 Hz',32,209,14,'#E0948E');
  graph(c,247,24,'#E0948E',v);
  c.globalAlpha=1;
  label(c,'SUM',32,305,14,'#BCE2C3');
  graph(c,354,40,'#BCE2C3',x=>(a(x)+v(x))/2);
  c.globalAlpha=b;
  label(c,'4 pulses per second',31,425,26,'#F1EAD8');
  c.globalAlpha=1;
  label(c,'Visual phase slowed 20× · sound at actual pitch',32,457,12,'#8FA99C');
}'''

WAVE_SCORE = r'''import numpy as np

def compose(synth, timeline, spec):
    sr=spec["sample_rate"]
    total=spec["duration_samples"]
    events={e["id"]:e for e in timeline["events"]}
    first=synth.track()
    second=synth.track()
    f0=events["first-tone"]["frame"]
    f1=events["second-tone"]["frame"]
    start0=round(f0*spec["fps"]["den"]*sr/spec["fps"]["num"])
    start1=round(f1*spec["fps"]["den"]*sr/spec["fps"]["num"])
    n0=total-start0
    n1=total-start1
    a=.125*synth.oscillator(220,n0)*synth.envelope(n0,attack=.035,release=.28)
    b=.125*synth.oscillator(224,n1)*synth.envelope(n1,attack=.18,release=.28)
    # Centre both tones so summation is also physically present in each channel.
    synth.place(first,synth.pan(a,0),start0,"first-tone")
    synth.place(second,synth.pan(b,0),start1,"second-tone")
    return {"sine_220_hz":first,"sine_224_hz":second},synth.events
'''


def write_project(skill, workspace, project_id, output, events, js, score, brief, spec, design):
    subprocess.run([sys.executable,str(skill/"scripts/project.py"),"init","--workspace",str(workspace),"--id",project_id,"--mode","create"],check=True)
    project=workspace/"projects"/project_id
    cfg=json.loads((project/"project.json").read_text())
    cfg["output"]={**output,"colorPolicy":"srgb-to-bt709-limited"}
    cfg["seed"]=6247 if project_id=="packet-flow" else 8429
    cfg["renderEntry"]="src/main.js"
    cfg["scoreEntry"]="audio/score.py"
    cfg["fonts"]=[{"family":"Arial","source":"local","name":"Arial","weight":"400"}]
    cfg["audio"]={"source":"procedural","sampleRate":48000,"channels":2,"loudnessTarget":-14,"truePeakMax":-1,"narrationRequired":False,"requiredEvents":[e["id"] for e in events],"design":design}
    cfg["requiredChecks"]=["technical","content","motion","audio_numeric","listening"]
    cfg["testFixture"]={"kind":"authored_original_demo","userFeedback":False,"referenceReconstructionEvidence":False}
    (project/"project.json").write_text(json.dumps(cfg,indent=2)+"\n")
    timeline={"shots":[{"id":"continuous-explanation","f0":0,"f1":output["frames"]}],"events":events}
    (project/"timeline.json").write_text(json.dumps(timeline,indent=2)+"\n")
    for directory in ("src","audio"):(project/directory).mkdir(exist_ok=True)
    (project/"src/main.js").write_text(js+"\n")
    (project/"audio/score.py").write_text(score+"\n")
    (project/"BRIEF.md").write_text(brief+"\n")
    (project/"SPEC.md").write_text(spec+"\n")
    (project/"REPORT.md").write_text("# Production status\n\nSource authored. Rendering, numerical checks, continuous playback and listening have not yet been recorded. User acceptance is unknown. This is an original educational demo, not client content or reference-reconstruction evidence.\n")
    return project


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",required=True,type=Path)
    args=parser.parse_args()
    workspace=args.workspace.resolve()
    skill=Path(__file__).resolve().parents[1]
    packet_events=[{"id":i,"frame":f,"basis":"designed","source":"Authored demo: visual handoff and procedural audio anchor"} for i,f in [("routing-start",18),("routing-middle",44),("reassembled",74)]]
    first=write_project(skill,workspace,"packet-flow",{"width":640,"height":360,"fps":{"num":24,"den":1},"frames":96},packet_events,PACKET_JS,PACKET_SCORE,
        """# Brief — one message, many packets

This Agent-authored educational test helps a nontechnical viewer understand the conceptual relationship between one message and independently routed parts that are reassembled. It makes no product, reliability, speed, or universal network-routing claim. The on-screen footer explicitly labels a schematic illustration. The movement and elapsed time are illustrative.

The output choice is a four-second 640×360 landscape composition at 24 fps. This is a local demo decision. No reference, brand asset, external content, narration, music recording, or user preference is supplied. Arial 400 is a declared local font and must be checked before render. No actual user review has occurred.
""",
        """# Specification — spatial distribution and reassembly

Creative mechanism: preserve six colored parts while moving them from a message-shaped group through separated paths into a reassembled group. Spatial identity carries the explanation without a before/after jump cut. The diagram deliberately omits real protocol detail.

The one continuous section uses the timeline's routing-start, routing-middle, and reassembled events. All are designed timings, not reference measurements. Before routing-start the two endpoints and message parts establish the relationship; each part traverses a staggered path; after reassembled, the caption names the resulting action. The numeric anchors exist only in timeline.json.

Design choices: warm paper background, dark blue text, three part colors, mostly horizontal travel, fine route lines, no blur or camera movement. The drawing does not clip intentionally. Typography uses verified local Arial 400 at project-specific sizes. These decisions are limited to this explanatory diagram.

Sound: two short pitched/noise transients travel across the stereo field; a longer three-frequency resonance marks reassembly. The point is discrete spatial handoffs followed by a sustained result, without a beat grid. Oscillators and noise are generated in code; no input audio is read.

Checks to perform: all six parts retain identity; paths remain within bounds; labels and final caption remain legible; timeline anchors match generated PCM; encoded frame count and exact duration agree; normal-speed motion and actual listening require separate evidence. No prediction in this SPEC is a completed check.
""",{"intent":"Discrete spatial handoffs followed by sustained reassembly resonance","tempo":None})
    wave_events=[{"id":i,"frame":f,"basis":"designed","source":"Authored demo: addition of the second sine component"} for i,f in [("first-tone",0),("second-tone",30)]]
    second=write_project(skill,workspace,"wave-sum",{"width":480,"height":480,"fps":{"num":30000,"den":1001},"frames":120},wave_events,WAVE_JS,WAVE_SCORE,
        """# Brief — audible addition of two close frequencies

This Agent-authored educational test explains superposition: two close sine frequencies sum to an amplitude pattern whose beat frequency is their difference. The chosen 220 Hz and 224 Hz pair creates four amplitude pulses per second once both tones reach the same steady level. This is a mathematical demonstration, not a product or health claim.

The chosen output is 480×480 with 120 frames at 30000/1001 fps, giving 4.004 seconds. Visual phase is intentionally slowed by 20× and clearly labelled; production sound retains its actual pitch. The visualization is not an oscilloscope capture, and the illustration's instantaneous phase need not equal the separately enveloped sound.

No reference or external audio is provided. No narration is required. Arial 400 is a declared local dependency. The demonstration establishes an authored target; actual perception, listening and user acceptance remain unverified until separately evidenced.
""",
        """# Specification — mathematical superposition

Creative mechanism: maintain two separate horizontal component traces and their sum in a single coordinate layout. Adding the second component changes the lower trace, exposing a causal relationship without travelling objects or scene transitions. This is intentionally different from packet-flow's spatial reassembly.

The timeline defines first-tone and second-tone. The second graph becomes prominent as the second generated sine enters. Visual phase is slowed 20× for readability; on-screen text discloses this. Two plotted components use 220 and 224 in the mathematical phase formula; the sum uses half their combined amplitude to stay within the lower plot. All timings are designed, not measured from a reference.

Design choices: deep green field, three separated plot bands, distinct amber/rose/mint trace roles, fixed typography and fine baselines. No moving camera, particles, blur, spatial routing, or stock materials. The final statement gives the frequency difference. The graphic is a conceptual phase view, not measurement evidence for the exported PCM.

Sound: two centered pure sine stems at 220 and 224 Hz, independently enveloped at start/end. The second gradually joins the first; physical signal addition in both channels produces beating. There is no beat sequencer, percussion, arbitrary musical cadence or stereo separation that would remove the intended summation.

Checks to perform: formula and plotted roles are correct; fractional fps and audio length agree; no clipped trace or unreadable label; cue onset is checked from actual PCM; final encoded color/time base is checked; continuous motion and actual listening remain separate reviews. User acceptance is unknown.
""",{"intent":"Two sine components sum to audible amplitude beating","frequenciesHz":[220,224],"visualTimeScale":.05})
    print(json.dumps({"projects":[str(first),str(second)],"status":"source_authored_not_rendered"},indent=2))


if __name__=="__main__":
    main()
