#!/usr/bin/env python3
"""Reference-only measurement and evidence gates. No creative/runtime templates."""
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import sys
import numpy as np
from PIL import Image
from _common import read_json, safe_path, sha256, cli_main, ident

SECTIONS = ('cuts','shots','components','text','colors','typography','cursor','camera','motion','audio','replacements')
STATES = {'measured','reviewed','inferred','unknown','not_applicable'}


def save_new(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    body=json.dumps(data,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
    if path.exists():
        if path.read_text()!=body: raise ValueError('Evidence exists; choose a new output/version: '+str(path))
    else:
        with path.open('x',encoding='utf-8') as f:f.write(body)


def interval(value):
    a,b=map(int,value.split(':'))
    if a<0 or a>=b: raise ValueError('Use a nonempty half-open A:B range')
    return a,b


def roi(value):
    values=list(map(int,value.split(',')))
    if len(values)!=4 or min(values[:2])<0 or min(values[2:])<=0:raise ValueError('ROI is X,Y,W,H in native pixels')
    return values


def crop(array, box):
    x,y,w,h=box
    if x<0 or y<0 or w<=0 or h<=0 or x+w>array.shape[1] or y+h>array.shape[0]:raise ValueError('ROI outside native frame')
    return array[y:y+h,x:x+w]


def reference(project):
    project=Path(project).resolve();c=read_json(project/'project.json');i=read_json(project/'ref/index.json')
    if c.get('mode')!='match' or c['reference']['sha256']!=i['sourceSha256']:raise ValueError('Actual matching reference analysis required')
    return project,i


def frame(project,index,number):
    if type(number) is not int or not 0<=number<index['frames']:raise ValueError('Frame outside reference')
    p=safe_path(project,f'ref/frames/{number:06d}.png');digest=sha256(p)
    if digest!=index['decode']['frameHashes'][str(number)]:raise ValueError('Reference frame hash changed')
    with Image.open(p) as im:
        if im.size!=(index['width'],index['height']):raise ValueError('Reference dimensions changed')
        return np.asarray(im.convert('RGB')),digest


def evidence_base(index,kind,method):
    return {'schemaVersion':1,'kind':kind,'referenceSha256':index['sourceSha256'],'size':[index['width'],index['height']],
            'frames':index['frames'],'fps':index['fps'],'method':method,'semanticReview':'unverified','interpolated':False}


def local_peaks(values,minimum=0.0,prominence=0.0,radius=2,min_distance=2):
    """Local maxima incl. plateaus; deterministic nonmaximum suppression. Candidates only."""
    v=np.asarray(values,dtype=float)
    if v.ndim!=1 or not np.isfinite(v).all() or minimum<0 or prominence<0 or radius<1 or min_distance<1:raise ValueError('Invalid peak parameters')
    candidates=[]
    for k in range(1,len(v)):
        if v[k]==v[k-1]:continue
        end=k
        while end+1<len(v) and v[end+1]==v[k]:end+=1
        lo=max(0,k-radius);hi=min(len(v),end+radius+1);window=v[lo:hi]
        if v[k]<minimum or v[k]<=0 or v[k]!=max(window):continue
        if any(v[j]==v[k] for j in range(lo,k)):continue
        baseline=max(min(v[lo:k]),min(v[end+1:hi])) if end+1<hi else min(v[lo:k])
        if v[k]-baseline>=prominence:candidates.append({'frame':k,'mad':float(v[k]),'prominence':float(v[k]-baseline)})
    selected=[]
    for row in sorted(candidates,key=lambda r:(-r['mad'],r['frame'])):
        if all(abs(row['frame']-x['frame'])>=min_distance for x in selected):selected.append(row)
    return sorted(selected,key=lambda r:r['frame'])


def cuts(args):
    p,i=reference(args.project);raw=read_json(p/'ref/cuts.json');values=[r['meanAbsoluteDifference'] for r in raw['scores']]
    result=evidence_base(i,'cuts','Adjacent native RGB mean absolute difference; local maxima + neighborhood prominence + nonmaximum suppression')
    result.update(parameters={'minimum':args.minimum,'prominence':args.prominence,'radius':args.radius,'minDistance':args.distance},
                  scoresSha256=sha256(p/'ref/cuts.json'),candidates=local_peaks(values,args.minimum,args.prominence,args.radius,args.distance),
                  reviewRequired=True,claim='Neither peaks nor absence of peaks establish editorial cuts. Inspect flashes, motion and gradual transitions separately.')
    save_new(safe_path(p,args.out),result);print(json.dumps({'candidates':result['candidates'],'out':args.out}))


def color(args):
    p,i=reference(args.project);im,h=frame(p,i,args.frame);box=roi(args.roi);pixels=crop(im,box).reshape(-1,3).astype(float)
    med=np.median(pixels,axis=0);result=evidence_base(i,'color','Native decoded RGB8 ROI statistics; no perceptual palette inference')
    result.update(frame=args.frame,imageSha256=h,roi=box,count=len(pixels),medianRGB=med.tolist(),meanRGB=pixels.mean(0).tolist(),
                  p05RGB=np.percentile(pixels,5,axis=0).tolist(),p95RGB=np.percentile(pixels,95,axis=0).tolist(),
                  medianHex='#'+''.join(f'{int(round(v)):02x}' for v in med),colorSpace='FFmpeg-decoded PNG RGB8; source color metadata retained in ref/index.json')
    save_new(safe_path(p,args.out),result);print(json.dumps(result))


def ink_mask(im,box,background,tolerance):
    values=crop(im,box).astype(np.int16)
    if len(background)!=3 or any(not 0<=v<=255 for v in background) or not 0<=tolerance<=255:raise ValueError('Invalid background/threshold')
    mask=np.max(np.abs(values-np.array(background,dtype=np.int16)),axis=2)>tolerance
    ys,xs=np.nonzero(mask)
    return None if not len(xs) else {'x':int(xs.min()+box[0]),'y':int(ys.min()+box[1]),'w':int(xs.max()-xs.min()+1),'h':int(ys.max()-ys.min()+1),'pixels':len(xs)}


def ink(args):
    p,i=reference(args.project);im,h=frame(p,i,args.frame);box=roi(args.roi);bg=list(map(int,args.background.split(',')))
    bounds=ink_mask(im,box,bg,args.tolerance);result=evidence_base(i,'ink','Absolute RGB distance from explicitly selected background; tight ROI foreground bounds')
    result.update(frame=args.frame,imageSha256=h,roi=box,backgroundRGB=bg,tolerance=args.tolerance,bbox=bounds,
                  glyphs=args.glyphs,measurement='Foreground height; cap height only if ROI contains verified untransformed uppercase glyphs without effects.',fontSizePx=None,
                  confidence='requires ROI/background/glyph visual validation; shadows and gradients contaminate the mask')
    if args.cap_ratio is not None:
        if not 0<args.cap_ratio<=2 or not args.font_basis:raise ValueError('Font cap ratio needs a measured candidate font basis')
        result.update(candidateCapRatio=args.cap_ratio,fontBasis=args.font_basis,fontSizeEstimatePx=bounds['h']/args.cap_ratio if bounds else None,
                      fontSizeEstimateStatus='derived estimate, not uniquely identified source font size')
    save_new(safe_path(p,args.out),result);print(json.dumps(result))


def template_search(search,template,minimum=.8,margin=.02):
    """Native grayscale ZNCC using FFT correlation and integral sums."""
    a=np.asarray(search,dtype=np.float64);b=np.asarray(template,dtype=np.float64)
    if a.ndim==3:a=a.mean(2)
    if b.ndim==3:b=b.mean(2)
    th,tw=b.shape;h,w=a.shape
    if th>h or tw>w:raise ValueError('Template larger than search ROI')
    b=b-b.mean();energy=float((b*b).sum())
    if energy<1e-8:raise ValueError('Flat template has no identifiable spatial structure')
    shape=tuple(1 << (v-1).bit_length() for v in (h+th-1,w+tw-1))
    numerator=np.fft.irfft2(np.fft.rfft2(a,s=shape)*np.fft.rfft2(b[::-1,::-1],s=shape),s=shape)[th-1:h,tw-1:w]
    def sums(values):
        integ=np.pad(values,((1,0),(1,0))).cumsum(0).cumsum(1)
        return integ[th:,tw:]-integ[:-th,tw:]-integ[th:,:-tw]+integ[:-th,:-tw]
    total=sums(a);denom=np.sqrt(np.maximum(sums(a*a)-total*total/(th*tw),0)*energy)
    scores=np.clip(np.divide(numerator,denom,out=np.full_like(numerator,-1),where=denom>1e-8),-1,1)
    y,x=np.unravel_index(scores.argmax(),scores.shape);best=float(scores[y,x]);other=scores.copy()
    ry,rx=max(1,th//2),max(1,tw//2);other[max(0,y-ry):y+ry+1,max(0,x-rx):x+rx+1]=-1
    second=float(other.max());accepted=best>=minimum and best-second>=margin
    return {'offset':[int(x),int(y)] if accepted else None,'score':best,'secondScore':second,'margin':best-second,
            'reason':None if accepted else 'below score threshold or ambiguous repeated template'}


def track(args):
    p,i=reference(args.project);start,end=interval(args.range);box=roi(args.search);tb=roi(args.template)
    if end>i['frames']:raise ValueError('Tracking range exceeds reference')
    im,h=frame(p,i,args.template_frame);template=crop(im,tb);anchor=list(map(float,args.anchor.split(',')))
    if len(anchor)!=2 or not 0<=anchor[0]<tb[2] or not 0<=anchor[1]<tb[3]:raise ValueError('Anchor must be inside template, relative to crop')
    if not -1<=args.minimum<=1 or not 0<=args.margin<=2:raise ValueError('Invalid ZNCC thresholds')
    rows=[]
    for number in range(start,end):
        current,digest=frame(p,i,number);r=template_search(crop(current,box),template,args.minimum,args.margin);off=r.pop('offset')
        b=None if off is None else {'x':box[0]+off[0],'y':box[1]+off[1],'w':tb[2],'h':tb[3]}
        rows.append({'frame':number,'imageSha256':digest,'bbox':b,'point':None if b is None else [b['x']+anchor[0],b['y']+anchor[1]],**r})
    result=evidence_base(i,'track','Native fixed-template grayscale ZNCC; no gap filling, scaling search or prediction')
    result.update(object=args.object,measuredProperties=['x','y'],range=[start,end],template={'frame':args.template_frame,'roi':tb,'imageSha256':h,'anchor':anchor},searchROI=box,
                  threshold=args.minimum,minimumMargin=args.margin,coverage=sum(x['point'] is not None for x in rows)/len(rows),observations=rows,
                  limits='Template bounds are a search window, not measured object size. Identity, anchor and visibility require review; split template versions for scale/shape changes. Low-confidence/occluded frames remain null.')
    save_new(safe_path(p,args.out),result);print(json.dumps({'out':args.out,'coverage':result['coverage']}))


def similarity_fit(source,target):
    x=np.asarray(source,dtype=float);y=np.asarray(target,dtype=float)
    if x.shape!=y.shape or x.ndim!=2 or x.shape[1]!=2 or len(x)<2 or not np.isfinite(x).all() or not np.isfinite(y).all():raise ValueError('Two or more finite matching 2D landmarks required')
    xc=x-x.mean(0);yc=y-y.mean(0);energy=(xc*xc).sum()
    if energy<1e-10:raise ValueError('Degenerate source landmarks')
    # Uniform scale + translation only; rotation/object motion must remain visible as residuals.
    scale=float((xc*yc).sum()/energy)
    if scale<=0:raise ValueError('Nonpositive similarity scale')
    tr=y.mean(0)-scale*x.mean(0);errors=np.linalg.norm(scale*x+tr-y,axis=1)
    return {'scale':scale,'tx':float(tr[0]),'ty':float(tr[1]),'maxResidualPx':float(errors.max()),'rmsResidualPx':float(np.sqrt((errors*errors).mean()))}


def camera(args):
    p,i=reference(args.project);paths=[safe_path(p,f) for f in args.tracks.split(',')];tracks=[read_json(f) for f in paths]
    if not math.isfinite(args.residual) or args.residual<0:raise ValueError('Residual threshold must be finite and nonnegative')
    if len(tracks)<2 or any(t.get('kind')!='track' or t.get('referenceSha256')!=i['sourceSha256'] for t in tracks):raise ValueError('At least two reference-bound point tracks required')
    for t in tracks:
        for row in t['observations']:
            if row.get('imageSha256')!=i['decode']['frameHashes'].get(str(row.get('frame'))) or not row.get('imageSha256'):raise ValueError('Landmark not bound to indexed reference frame')
    tables=[{x['frame']:x.get('point') for x in t['observations']} for t in tracks];anchors=[t.get(args.anchor_frame) for t in tables]
    if any(a is None for a in anchors):raise ValueError('Camera anchor missing in a landmark track')
    rows=[]
    for f in sorted(set().union(*(t.keys() for t in tables))):
        points=[t.get(f) for t in tables]
        if any(x is None for x in points):rows.append({'frame':f,'model':None,'reason':'missing landmark'});continue
        try:fit=similarity_fit(anchors,points)
        except ValueError as exc:rows.append({'frame':f,'model':None,'reason':str(exc)});continue
        ok=fit['maxResidualPx']<=args.residual
        rows.append({'frame':f,'model':fit if ok else None,'candidate':fit,'reason':None if ok else 'landmarks disagree with uniform scale/translation model'})
    result=evidence_base(i,'camera','Least-squares scale/translation fit to two or more measured landmarks; camera interpretation is inferred')
    result.update(anchorFrame=args.anchor_frame,tracks=[{'path':str(f.relative_to(p)),'sha256':sha256(f)} for f in paths],maxResidualPx=args.residual,
                  observations=rows,coverage=sum(r['model'] is not None for r in rows)/len(rows),modelStatus='inferred; component motion may explain the same screen-space tracks')
    save_new(safe_path(p,args.out),result);print(json.dumps({'out':args.out,'coverage':result['coverage']}))


def audio_candidates(args):
    p,i=reference(args.project);result=evidence_base(i,'audio_candidates','10ms hop spectral positive log-flux; onset candidates and autocorrelation period candidates, not source separation')
    if not i['hasAudio']:result.update(status='not_applicable',reason='Reference contains no audio stream');save_new(safe_path(p,args.out),result);return
    path=read_json(p/'project.json')['reference']['path'];path=Path(path) if Path(path).is_absolute() else safe_path(p,path)
    if sha256(path)!=i['sourceSha256']:raise ValueError('Reference changed')
    raw=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-ac','1','-ar','16000','-f','f32le','pipe:1'],check=True,capture_output=True).stdout
    pcm=np.frombuffer(raw,dtype='<f4').astype(float);hop=160;size=512
    if len(pcm)<size:raise ValueError('Insufficient audio for onset analysis')
    windows=np.lib.stride_tricks.sliding_window_view(pcm,size)[::hop];spectra=np.log1p(abs(np.fft.rfft(windows*np.hanning(size),axis=1)))
    flux=np.concatenate(([0.],np.maximum(np.diff(spectra,axis=0),0).sum(1)));med=float(np.median(flux));dev=float(np.median(abs(flux-med)))
    peaks=local_peaks(flux,med+3*dev,dev,3,6)
    centered=flux-flux.mean();lags=range(25,min(len(flux)-1,201));values=[float(np.dot(centered[:-k],centered[k:])) for k in lags]
    top=[]
    for pos in sorted(range(len(values)),key=lambda k:-values[k]):
        lag=list(lags)[pos]
        if values[pos]>0 and all(abs(lag-x['lagHops'])>=3 for x in top):top.append({'lagHops':lag,'bpm':6000/lag,'score':values[pos]})
        if len(top)>=5:break
    result.update(status='candidates_only',sampleRate=16000,hopSeconds=.01,sourceAudioOffsetSeconds=i.get('audioOffsetSeconds'),
                  onsets=[{'seconds':x['frame']*.01+size/32000,'strength':x['mad']} for x in peaks],tempoCandidates=top,
                  rms=[{'seconds':j*hop/16000,'rms':float(v)} for j,v in enumerate(np.sqrt((windows*windows).mean(1)))],
                  beatPhase='unverified; autocorrelation alone does not identify downbeat/phase',voice={'transcript':'unknown','pitch':'unknown','wpm':'unknown'},
                  limits='Onsets can be percussion, speech or SFX. Silence, voice absence, drops and all-hit coverage require separate review. No external STT/TTS/music service invoked.')
    save_new(safe_path(p,args.out),result);print(json.dumps({'out':args.out,'onsets':len(peaks),'tempoCandidates':top}))


def coverage_ranges(ranges,count):
    covered=set()
    for r in ranges:
        if not isinstance(r,list) or len(r)!=2 or any(type(x) is not int for x in r) or not 0<=r[0]<r[1]<=count:raise ValueError('Invalid reference visibility/review range')
        new=set(range(*r))
        if covered&new:raise ValueError('Overlapping declared ranges')
        covered|=new
    return covered


def analysis_gate(project,config=None,stage='production'):
    """Validate structured evidence, never infer actual perception from JSON assertions."""
    p=Path(project);c=config or read_json(p/'project.json')
    if c['mode']!='match':return {'status':'not_applicable','issues':[]}
    file=p/'analysis/match.json';issues=[];unknown=[]
    if not file.is_file():return {'status':'blocked','issues':['Author analysis/match.json before match production'],'unknown':[]}
    d=read_json(file);ref=c['reference']['sha256'];n=c['output']['frames']
    if d.get('schemaVersion')!=1 or d.get('referenceSha256')!=ref:issues.append('Analysis schema/reference hash mismatch')
    if d.get('output')!=c['output']:issues.append('Analysis time/geometry/color contract differs from project')
    index={}
    try:
        link=d['referenceIndex'];f=safe_path(p,link['path'])
        if not link['path'].startswith('analysis/') or link.get('sha256')!=sha256(f):raise ValueError('Frozen reference index hash mismatch')
        index=read_json(f)
        if index.get('sourceSha256')!=ref or index.get('frames')!=n or index.get('decode',{}).get('coverage')!=1:raise ValueError('Complete reference index required')
        if (index.get('width'),index.get('height'))!=(c['output']['width'],c['output']['height']) or Fraction(index['fps']['num'],index['fps']['den'])!=Fraction(c['output']['fps']['num'],c['output']['fps']['den']):raise ValueError('Reference index time/size mismatch')
        hashes=index['decode']['frameHashes']
        if set(hashes)!=set(map(str,range(n))) or any(not isinstance(h,str) or len(h)!=64 or any(ch not in '0123456789abcdef' for ch in h) for h in hashes.values()):raise ValueError('Complete per-frame SHA-256 map required')
    except (ValueError,KeyError,OSError,TypeError) as exc:issues.append('referenceIndex: '+str(exc))
    documents={}
    for name in SECTIONS:
        section=d.get(name)
        if not isinstance(section,dict) or section.get('state') not in STATES or not section.get('reason') or not isinstance(section.get('items'),list) or any(not isinstance(x,dict) for x in section.get('items',[])):
            issues.append('Incomplete section: '+name);continue
        state=section['state']
        if state in ('unknown','inferred'):unknown.append(name)
        if state in ('measured','reviewed') and not section.get('evidence'):issues.append('Evidence required: '+name)
        for link in section.get('evidence',[]):
            try:
                relative=link['path']
                if not relative.startswith('analysis/') or relative=='analysis/match.json':raise ValueError('Evidence must be independent under analysis/')
                f=safe_path(p,relative)
                if f.suffix!='.json' or link.get('sha256')!=sha256(f):raise ValueError('Evidence hash/type mismatch')
                data=read_json(f)
                if data.get('referenceSha256')!=ref:raise ValueError('Evidence belongs to another reference')
                if data.get('kind') in ('color','ink','track'):
                    observed=data.get('observations',[data])
                    seen=set()
                    for row in observed:
                        number=row.get('frame')
                        if type(number) is not int or not 0<=number<n or number in seen or not row.get('imageSha256') or row.get('imageSha256')!=index.get('decode',{}).get('frameHashes',{}).get(str(number)):
                            raise ValueError('Pixel evidence not bound to indexed frame')
                        seen.add(number)
                        if row.get('bbox') is not None and (not all(type(row['bbox'].get(k)) in (int,float) and math.isfinite(row['bbox'][k]) for k in ('x','y','w','h')) or min(row['bbox']['w'],row['bbox']['h'])<=0):raise ValueError('Invalid measured bounds')
                        if row.get('point') is not None and (len(row['point'])!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in row['point'])):raise ValueError('Invalid measured point')
                    if data.get('kind')=='track':
                        props=data.get('measuredProperties',[])
                        if not props or any(k not in ('x','y','w','h') for k in props):raise ValueError('Invalid track properties')
                        if 'template' in data:
                            td=data['template']
                            if set(props)-{'x','y'} or td.get('imageSha256')!=index['decode']['frameHashes'].get(str(td.get('frame'))):raise ValueError('Template cannot establish object size; indexed template frame required')
                documents[relative]=data
            except (ValueError,KeyError,OSError,TypeError) as exc:issues.append(name+': '+str(exc))
    if any(x.startswith('Incomplete section:') for x in issues):
        return {'status':'blocked','issues':issues,'unknown':unknown,'allowed':False}
    def items(name):
        section=d.get(name,{})
        return [x for x in section.get('items',[]) if isinstance(x,dict)] if isinstance(section,dict) and isinstance(section.get('items',[]),list) else []
    shots=items('shots');cursor=0;ids=set()
    for shot in shots:
        if not all(shot.get(k) for k in ('id','content','entrance','exit','details')) or shot.get('id') in ids or shot.get('f0')!=cursor or type(shot.get('f1')) is not int or not cursor<shot['f1']<=n:issues.append('Shot table must detail and cover [0,N) exactly once');break
        cursor=shot['f1'];ids.add(shot['id'])
    if cursor!=n:issues.append('Shot table missing/incomplete')
    timeline=read_json(p/'timeline.json') if (p/'timeline.json').exists() else {}
    if [(s.get('id'),s.get('f0'),s.get('f1')) for s in shots]!=[(s.get('id'),s.get('f0'),s.get('f1')) for s in timeline.get('shots',[])]:issues.append('Shot table differs from authored timeline')
    components=items('components');component_ids={x.get('id') for x in components}
    if not components or None in component_ids or len(component_ids)!=len(components):issues.append('Unique component inventory required')
    if not any(x.get('critical') is True for x in components):unknown.append('critical component geometry not declared')
    for comp in components:
        try:
            if not comp.get('kind') or type(comp.get('critical')) is not bool:raise ValueError('Component kind/critical declaration missing')
            required=coverage_ranges(comp['visibleRanges'],n)
            if not required:raise ValueError('Component visibility empty')
            if comp['critical']:
                properties=comp.get('properties',[])
                if not properties or any(k not in ('x','y','w','h') for k in properties):raise ValueError('Declare required critical geometry properties')
                paths=comp.get('tracks',[]);observed={k:set() for k in properties}
                for path in paths:
                    track_data=documents.get(path,{})
                    if track_data.get('kind')!='track' or track_data.get('interpolated') is not False:raise ValueError('Critical component needs actual non-interpolated track evidence')
                    if 'detector' not in track_data and 'template' not in track_data:raise ValueError('Unsupported track measurement method')
                    for row in track_data.get('observations',[]):
                        if row.get('point') is not None or row.get('bbox') is not None:
                            for k in set(track_data.get('measuredProperties',[]))&set(properties):observed[k].add(row['frame'])
                if any(not required<=observed[k] for k in properties):unknown.append('component:'+comp['id']+':coverage')
        except (ValueError,KeyError,TypeError) as exc:issues.append(str(exc))
    for row in items('text'):
        if not row.get('id') or not isinstance(row.get('text'),str) or not row['text'] or type(row.get('f0')) is not int or type(row.get('f1')) is not int or not 0<=row['f0']<row['f1']<=n or row.get('component') not in component_ids:issues.append('Text needs id, verbatim content, component and visible interval')
        previous=-1
        for e in row.get('characterEvents',[]):
            if type(e.get('frame')) is not int or not 0<=e['frame']<n or e['frame']<=previous or type(e.get('count')) is not int or not 0<=e['count']<=len(row.get('text','')):issues.append('Invalid character timing event')
            previous=e.get('frame',-1)
    locks=set()
    for event in d.get('events',[]):
        if not isinstance(event,dict) or not event.get('id') or event['id'] in locks or str(event['id']).startswith(('text:','cut:')) or type(event.get('frame')) is not int or not 0<=event['frame']<n or not event.get('basis'):
            issues.append('Discrete visual locks need unique id, reference frame and evidence basis');continue
        locks.add(event['id'])
        if event['basis'] in ('inferred','unknown'):unknown.append('event:'+event['id'])
    for name,kind in [('colors','color'),('typography','ink')]:
        if d.get(name,{}).get('state')=='reviewed':issues.append(name+' requires measured evidence or an explicit unknown/inferred state')
        if d.get(name,{}).get('state')=='measured':
            if not items(name):issues.append('Measured section has no items: '+name)
            for row in items(name):
                measured=documents.get(row.get('measurement'),{})
                if measured.get('kind')!=kind:issues.append(name+' needs actual '+kind+' evidence')
                elif kind=='ink' and measured.get('bbox') is None:unknown.append('typography:missing ink')
                elif kind=='color' and (type(measured.get('count')) is not int or measured['count']<=0 or not isinstance(measured.get('medianRGB'),list) or len(measured['medianRGB'])!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=255 for v in measured['medianRGB'])):issues.append('Invalid measured color sample')
    for name in ('cursor','motion'):
        if d.get(name,{}).get('state')=='measured':
            for row in items(name):
                if documents.get(row.get('measurement'),{}).get('kind')!='track':issues.append(name+' needs actual per-frame track evidence')
    if d.get('cursor',{}).get('state')=='measured' and not items('cursor'):issues.append('Measured cursor needs trajectories')
    if d.get('camera',{}).get('state')=='measured':issues.append('Camera interpretation is inferred; use reviewed with measured screen-space evidence')
    if d.get('camera',{}).get('state')=='reviewed':
        if not items('camera'):issues.append('Reviewed camera needs landmark evidence or explicit not_applicable')
        for row in items('camera'):
            if documents.get(row.get('measurement'),{}).get('kind')!='camera':issues.append('Camera needs landmark fit evidence')
    for document in list(documents.values()):
        if document.get('kind')=='camera':
            for link in document.get('tracks',[]):
                track_data=documents.get(link.get('path'),{})
                try:
                    if track_data.get('kind')!='track' or sha256(safe_path(p,link['path']))!=link.get('sha256'):raise ValueError('Camera source tracks must also be declared and hash-checked evidence')
                except (ValueError,KeyError,OSError) as exc:issues.append(str(exc))
    cut_docs=[v for v in documents.values() if v.get('kind')=='cuts']
    decisions={x.get('frame'):x for x in items('cuts')}
    if d.get('cuts',{}).get('state')=='reviewed':
        if not cut_docs:issues.append('Cut review needs actual candidate report')
        for data in cut_docs:
            for row in data.get('candidates',[]):
                decision=decisions.get(row['frame'],{})
                if decision.get('decision') not in ('hard_cut','transition','flash','motion','no_cut') or not decision.get('reason'):unknown.append('cut:'+str(row['frame']))
        for f,decision in decisions.items():
            if decision.get('decision')=='hard_cut' and f not in [s.get('f0') for s in shots[1:]]:issues.append('Confirmed hard cut missing from shot table')
    review=d.get('review',{})
    try:
        viewed=coverage_ranges(review.get('ranges',[]),n)
        if not review.get('reviewer') or not review.get('method') or not review.get('observations'):unknown.append('visual review')
        if len(viewed)<n:unknown.append('visual review coverage')
    except ValueError as exc:issues.append(str(exc))
    # not_applicable is an explicit scoped observation, never a missing field shorthand.
    for name in SECTIONS:
        section=d.get(name,{})
        if not isinstance(section,dict):continue
        if section.get('state')=='not_applicable':
            if name in ('cuts','shots','components','colors'):issues.append(name+' cannot be not_applicable for a visual reference')
            if section.get('items'):issues.append('not_applicable section must not contain active items: '+name)
            if not section.get('evidence'):issues.append('not_applicable needs observational evidence: '+name)
    for name in ('text','typography','cursor','camera','motion','audio','replacements'):
        if d.get(name,{}).get('state') in ('measured','reviewed') and not items(name):issues.append('Active section has no items: '+name)
    probe_ready=not issues;ready=probe_ready and not unknown
    return {'status':'ready' if ready else ('probe_ready' if probe_ready else 'blocked'),'issues':issues,'unknown':sorted(set(unknown)),
            'allowed':ready if stage=='production' else probe_ready,'analysisSha256':sha256(file),
            'claim':'Structural/evidence gate only; records do not prove truthful viewing, object identity, listening or fidelity.'}


def gate(args):
    p=Path(args.project).resolve();c=read_json(p/'project.json');result=analysis_gate(p,c,args.stage)
    if args.out:save_new(safe_path(p,args.out),result)
    print(json.dumps(result,indent=2,ensure_ascii=False))
    if not result.get('allowed',c['mode']=='create'):raise ValueError('Match analysis gate not ready for '+args.stage)


def spec(args):
    p=Path(args.project).resolve();c=read_json(p/'project.json');result=analysis_gate(p,c,'probe')
    if result['status']=='blocked':raise ValueError('Repair analysis structure before generating SPEC: '+str(result['issues']))
    data=read_json(p/'analysis/match.json');parts=['# Reference analysis specification',f"Reference SHA-256: `{c['reference']['sha256']}`",f"Analysis state: **{result['status']}**. Unknown: {', '.join(result['unknown']) or 'none declared'}.",'Frame intervals are native zero-based [f0,f1). Structured measurements remain the source of numeric values.']
    for name in SECTIONS:
        s=data[name];parts+=['## '+name,s['state']+' — '+s['reason']]
        for item in s['items']:parts+=['```json',json.dumps(item,indent=2,ensure_ascii=False),'```']
        parts+=['Evidence: '+', '.join('`'+e['path']+'`' for e in s.get('evidence',[]))]
    target=safe_path(p,args.out)
    if target.exists():raise ValueError('Do not overwrite reviewed SPEC; choose a new output')
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text('\n\n'.join(parts)+'\n');print(target)


def locked_event_check(source,out,artifact_hash):
    d=read_json(source/'analysis/match.json');expected={}
    for item in d['cuts']['items']:
        if item.get('decision')=='hard_cut':expected['cut:'+str(item['frame'])]=item['frame']
    for item in d['text']['items']:
        for pos,event in enumerate(item.get('characterEvents',[])):expected['text:'+item['id']+':'+str(pos)]=event['frame']
    for item in d.get('events',[]):expected[item['id']]=item['frame']
    if not expected:return {'status':'not_applicable','reason':'No declared discrete visual locks; per-frame trajectories checked separately','events':[]}
    file=out/'review/events.json'
    if not file.exists():return {'status':'unverified','reason':'Actual output event observations missing','expected':expected}
    evidence=read_json(file)
    if evidence.get('artifactSha256')!=artifact_hash or not all(evidence.get(k) for k in ('reviewer','method','observations')):return {'status':'unverified','reason':'Event evidence lacks actual artifact/reviewer/method/observations'}
    observations={e['id']:e for e in evidence.get('events',[])};rows=[]
    for name,f in expected.items():
        actual=observations.get(name,{}).get('actualFrame');valid=type(actual) is int
        rows.append({'id':name,'referenceFrame':f,'actualFrame':actual,'errorFrames':actual-f if valid else None,'status':'pass' if valid and actual==f else ('fail' if valid else 'unverified')})
    return {'status':'pass' if all(r['status']=='pass' for r in rows) else ('fail' if any(r['status']=='fail' for r in rows) else 'unverified'),
            'events':rows,'evidenceSha256':sha256(file),'limits':'Artifact binding and frame arithmetic checked; truthful perceptual observations remain reviewer responsibility.'}


def verify(args):
    from _common import load_revision,revision_output
    from measure import verify_frame_evidence,detect
    p=Path(args.project).resolve();source,c,t,manifest=load_revision(p,args.revision,require_tools=True)
    if c['mode']!='match' or c.get('renderPurpose')=='analysis-probe':raise ValueError('Production match revision required')
    out=revision_output(p,args.revision);o=c['output'];verify_frame_evidence(out,manifest,o['frames'])
    artifact=out/'preview.mp4'
    if not artifact.exists():raise ValueError('Encode actual preview before binding match verification')
    d=read_json(source/'analysis/match.json');index=read_json(safe_path(source,d['referenceIndex']['path']));results=[]
    paths=sorted(set(path for comp in d['components']['items'] if comp['critical'] for path in comp['tracks']))
    for path in paths:
        track_data=read_json(safe_path(source,path));needed={}
        for comp in d['components']['items']:
            if comp['critical'] and path in comp['tracks']:
                for k in set(comp['properties'])&set(track_data['measuredProperties']):needed.setdefault(k,set()).update(coverage_ranges(comp['visibleRanges'],o['frames']))
        props=sorted(needed);required=set().union(*needed.values());rows=[];maximum={k:{'error':0.,'frame':None} for k in props};valid=0
        template=None
        if 'template' in track_data:
            td=track_data['template'];im,_=frame(p,index,td['frame']);template=crop(im,td['roi'])
        for ref in track_data['observations']:
            f=ref['frame'];file=out/'frames'/f'{f:06d}.png'
            if f not in required:continue
            with Image.open(file) as im:
                if template is None:
                    actual=detect(im,track_data['detector']);wanted=ref.get('bbox')
                else:
                    arr=np.asarray(im.convert('RGB'));box=track_data['searchROI'];found=template_search(crop(arr,box),template,track_data['threshold'],track_data['minimumMargin']);off=found['offset'];anchor=track_data['template']['anchor']
                    actual=None if off is None else {'x':box[0]+off[0]+anchor[0],'y':box[1]+off[1]+anchor[1]}
                    point=ref.get('point');wanted=None if point is None else dict(zip(('x','y'),point))
            errors=None
            if actual is not None and wanted is not None:
                valid+=1;errors={k:abs(actual[k]-wanted[k])/(o['width'] if k in ('x','w') else o['height']) for k in props if f in needed[k]}
                for k,v in errors.items():
                    if maximum[k]['frame'] is None or v>maximum[k]['error']:maximum[k]={'error':v,'frame':f}
            rows.append({'frame':f,'reference':wanted,'output':actual,'errors':errors,'outputSha256':sha256(file)})
        status='fail' if any(x['error']>.01 for x in maximum.values()) else ('pass' if valid==len(rows) else 'unverified')
        results.append({'evidence':path,'method':track_data['method'],'properties':props,'status':status,'validFrames':valid,'requestedFrames':len(rows),'maximum':maximum,'observations':rows})
    numerical='pass' if results and all(r['status']=='pass' for r in results) else ('fail' if any(r['status']=='fail' for r in results) else 'unverified')
    artifact_hash=sha256(artifact);events=locked_event_check(source,out,artifact_hash)
    status='pass' if numerical=='pass' and events['status'] in ('pass','not_applicable') else ('fail' if numerical=='fail' or events['status']=='fail' else 'unverified')
    result={'schemaVersion':1,'revision':args.revision,'fingerprint':manifest['fingerprint'],'artifactSha256':artifact_hash,'status':status,'numericStatus':numerical,'objects':results,'events':events,'toolSha256':sha256(Path(__file__)),
            'limits':'Required declared geometry/point tracks and discrete locks only. No automatic content, typography, color, camera interpretation, listening or whole-scene perceptual pass.'}
    save_new(out/'review/match-verification.json',result);print(json.dumps({'status':status,'numericStatus':numerical,'events':events['status']}))
    if status!='pass':raise ValueError('Match verification did not pass; retain preview and evidence')


def require_match_verification(project,revision,config,manifest,artifact_hash):
    out=Path(project)/'out'/revision;f=out/'review/match-verification.json';r=read_json(f)
    if r.get('status')!='pass' or r.get('artifactSha256')!=artifact_hash or r.get('fingerprint')!=manifest['fingerprint'] or r.get('toolSha256')!=sha256(Path(__file__)):raise ValueError('Current artifact-bound match verification required')
    source=Path(project)/'revisions'/revision/'source';events=locked_event_check(source,out,artifact_hash)
    if r.get('events')!=events or events['status'] not in ('pass','not_applicable'):raise ValueError('Visual lock observations changed or incomplete')


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    def command(name,fn):
        q=sub.add_parser(name);q.add_argument('--project',required=True,type=Path);q.add_argument('--out',required=name!='gate');q.set_defaults(fn=fn);return q
    q=command('cuts',cuts);q.add_argument('--minimum',type=float,default=8);q.add_argument('--prominence',type=float,default=4);q.add_argument('--radius',type=int,default=3);q.add_argument('--distance',type=int,default=3)
    q=command('color',color);q.add_argument('--frame',type=int,required=True);q.add_argument('--roi',required=True)
    q=command('ink',ink);q.add_argument('--frame',type=int,required=True);q.add_argument('--roi',required=True);q.add_argument('--background',required=True);q.add_argument('--tolerance',type=int,default=24);q.add_argument('--glyphs',required=True);q.add_argument('--cap-ratio',type=float);q.add_argument('--font-basis')
    q=command('track',track);q.add_argument('--object',required=True);q.add_argument('--range',required=True);q.add_argument('--template-frame',type=int,required=True);q.add_argument('--template',required=True);q.add_argument('--search',required=True);q.add_argument('--anchor',required=True);q.add_argument('--minimum',type=float,default=.85);q.add_argument('--margin',type=float,default=.03)
    q=command('camera',camera);q.add_argument('--tracks',required=True);q.add_argument('--anchor-frame',type=int,required=True);q.add_argument('--residual',type=float,default=2)
    command('audio-candidates',audio_candidates)
    q=command('gate',gate);q.add_argument('--stage',choices=['probe','production'],default='production')
    command('spec',spec)
    q=sub.add_parser('verify');q.add_argument('--project',required=True,type=Path);q.add_argument('--revision',required=True);q.set_defaults(fn=verify)
    args=parser.parse_args();args.fn(args)


if __name__=='__main__':cli_main(main)
