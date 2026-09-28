#!/usr/bin/env node
/** Empty Canvas host. Project code alone chooses every visual and timing decision. */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
const here = path.dirname(fileURLToPath(import.meta.url));
const hash = x => crypto.createHash('sha256').update(x).digest('hex');
const read = async p => JSON.parse(await fs.readFile(p,'utf8'));
const digest = async p => hash(await fs.readFile(p));
const inside = async (root, rel) => {
  const base = await fs.realpath(root), p = await fs.realpath(path.resolve(base, rel));
  if(p !== base && !p.startsWith(base + path.sep)) throw Error(`Path escapes snapshot: ${rel}`);
  return p;
};
const safeOutput = async (root, target) => {
  let existing=target;
  for(;;){try {await fs.lstat(existing);break;}catch(e){if(e.code!=='ENOENT')throw e;existing=path.dirname(existing);}}
  return inside(root,path.relative(root,existing));
};
const timed = async promise => {
  let timer;
  try {return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Project preparation/draw exceeded 30 seconds')),30000);})]);}
  finally{clearTimeout(timer);}
};
const args = process.argv.slice(2);
if(args.includes('--help') || !args.length) {
  console.log('render.mjs --project P --revision R [--browser PATH] [--python PATH] stills 0,12,24 | compare 0,12 | full A B\nFrame intervals are 0-based [A,B). stills/compare max 15 requested frames. Requires an immutable project snapshot. compare requires actual match reference frames. Writes PNGs and verifies reversed/repeated pixel output. RM_PLAYWRIGHT_MODULE may point to an installed playwright entry module; never installs a browser implicitly.');
  process.exit(0);
}
let browser, context, lock;
async function main() {
  const opts = {}, positional = [];
  for(let i=0;i<args.length;i++) {
    if(args[i].startsWith('--')) {
      if(!['--project','--revision','--browser','--python'].includes(args[i]) || !args[i+1]) throw Error('Unknown/missing option ' + args[i]);
      opts[args[i].slice(2)] = args[++i];
    } else positional.push(args[i]);
  }
  if(!opts.project || !/^[\w-]+$/.test(opts.revision || '')) throw Error('Explicit --project and valid --revision required');
  const project = await fs.realpath(opts.project), root = path.join(project,'revisions',opts.revision), source = await fs.realpath(path.join(root,'source'));
  if(!source.startsWith(project+path.sep)) throw Error('Snapshot escapes project');
  const manifest = await read(path.join(root,'manifest.json'));
  const config = await read(path.join(source,'project.json')), timeline = await read(path.join(source,'timeline.json'));
  // Python shared validation is the same entry contract used by other media scripts.
  const validation = spawnSync(opts.python || process.env.RM_PYTHON || 'python3', ['-B','-c','import sys;sys.path.insert(0,sys.argv[1]);from _common import load_revision;load_revision(sys.argv[2],sys.argv[3],require_tools=True)',here,project,opts.revision],{encoding:'utf8'});
  if(validation.status !== 0) throw Error(validation.stderr || 'Snapshot validation failed');
  for(const [rel,sha] of Object.entries(manifest.files)) if(await digest(await inside(source,rel))!==sha) throw Error('Snapshot changed: '+rel);
  if(config.revision!==opts.revision || config.id!==manifest.projectId) throw Error('Revision identity mismatch');
  const o = config.output, N = o.frames;
  let frames;
  const [command,a,b] = positional;
  if(command==='full') {
    const first = Number(a), end = Number(b);
    if(positional.length!==3 || !Number.isInteger(first) || !Number.isInteger(end) || first<0 || end>N || first>=end) throw Error('full requires valid [A,B) inside [0,N)');
    frames=Array.from({length:end-first},(_,i)=>first+i);
  } else if(['stills','compare'].includes(command)) {
    frames=(a||'').split(',').map(Number);
    if(positional.length!==2 || !a || frames.length>15 || frames.some(f=>!Number.isInteger(f)||f<0||f>=N)) throw Error('stills/compare needs 1–15 valid integer frame indices');
  } else throw Error('Expected stills, compare or full');
  if(config.renderPurpose==='analysis-probe' && (command==='full' || frames.some(f=>!config.probeFrames.includes(f)))) throw Error('Analysis probe is restricted to its explicit still/compare frames; finish the analysis gate before production');
  if(command==='compare' && (config.mode!=='match' || !config.reference)) throw Error('compare requires actual match reference; use sync revision for before/after');
  const out = path.join(project,'out',opts.revision), frameDir=path.join(out,'frames'), review=path.join(out,'review');
  await safeOutput(project,frameDir); await safeOutput(project,review);
  await fs.mkdir(frameDir,{recursive:true}); await fs.mkdir(review,{recursive:true});
  await inside(project,path.relative(project,frameDir)); await inside(project,path.relative(project,review));
  const lockPath=path.join(out,'.render.lock');
  lock=await fs.open(lockPath,'wx'); lock.pathName=lockPath;
  let playwright;
  if(process.env.RM_PLAYWRIGHT_MODULE) playwright=await import(pathToFileURL(process.env.RM_PLAYWRIGHT_MODULE));
  else playwright=await import('playwright');
  const serverErrors=[];
  // Only hash-listed modules and declared production assets are available, not the project or workspace.
  const allowed=new Set(Object.keys(manifest.files).filter(p=>p.startsWith('src/') || config.assets.some(a=>a.use==='production'&&a.path===p)));
  const token=crypto.randomBytes(18).toString('hex');
  const base=`http://reference-motion.invalid/${token}/`;
  const csp="default-src 'none'; script-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; media-src 'none'; object-src 'none'; frame-src 'none'";
  browser=await playwright.chromium.launch({headless:true,chromiumSandbox:true,...(opts.browser||process.env.RM_BROWSER?{executablePath:opts.browser||process.env.RM_BROWSER}:{})});
  context=await browser.newContext({viewport:{width:o.width,height:o.height},deviceScaleFactor:1,serviceWorkers:'block',acceptDownloads:false});
  const page=await context.newPage(), pageErrors=[];
  page.setDefaultTimeout(30000);
  page.on('pageerror',err=>pageErrors.push(err.message));
  page.on('requestfailed',req=>pageErrors.push('Failed resource '+req.url()));
  await context.route('**/*',async route=>{
    try {
      const url=route.request().url();
      if(!url.startsWith(base))throw Error('External resource denied: '+url);
      const rel=decodeURIComponent(new URL(url).pathname.slice(token.length+2));
      const headers={'Cache-Control':'no-store','Content-Security-Policy':csp};
      if(rel==='')return await route.fulfill({status:200,contentType:'text/html',headers,body:'<!doctype html><html><body style="margin:0"><canvas id="frame"></canvas></body></html>'});
      if(!allowed.has(rel))throw Error('Unlisted resource: '+rel);
      const data=await fs.readFile(await inside(source,rel));
      if(hash(data)!==manifest.files[rel])throw Error('Changed resource: '+rel);
      const mime={'.js':'text/javascript','.mjs':'text/javascript','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.woff2':'font/woff2','.ttf':'font/ttf','.otf':'font/otf','.json':'application/json'}[path.extname(rel)];
      if(!mime)throw Error('Unsupported resource type; SVG must be safely rasterized explicitly');
      await route.fulfill({status:200,contentType:mime,headers,body:data});
    }catch(e){serverErrors.push(e.message);await route.abort('blockedbyclient');}
  });
  await page.goto(base);
  const setup=await timed(page.evaluate(async({config,timeline,base})=>{
    const o=config.output, canvas=document.getElementById('frame');
    canvas.width=o.width;canvas.height=o.height;
    const ctx=canvas.getContext('2d',{alpha:true,colorSpace:'srgb',willReadFrequently:true});
    const freeze=x=>{if(x&&typeof x==='object'){Object.freeze(x);for(const v of Object.values(x))freeze(v);}return x;};
    const assets=new Map();
    const fonts=[];
    for(const f of config.fonts||[]) {
      const url=f.source==='local'?`local(${JSON.stringify(f.name||f.family)})`:`url(${JSON.stringify(base+f.path)})`;
      const face=new FontFace(f.family,url,{weight:f.weight||'400',style:f.style||'normal'});
      await face.load();document.fonts.add(face);fonts.push({family:f.family,status:face.status,source:f.source});
    }
    for(const a of config.assets||[]) {
      if(a.use!=='production')continue;
      if(a.type==='image') {const img=new Image();img.src=base+a.path;await img.decode();assets.set(a.id,img);}
      else if(a.type!=='font') throw Error('Unsupported production asset type '+a.type);
    }
    const readonlyAssets=Object.freeze({get:id=>assets.get(id),has:id=>assets.has(id),keys:()=>assets.keys()});
    const mod=await import(base+config.renderEntry);
    if(typeof mod.prepare!=='function'||typeof mod.drawFrame!=='function')throw Error('Project must export prepare and drawFrame');
    const common=freeze({width:o.width,height:o.height,fps:o.fps,seed:config.seed,timeline});
    await mod.prepare({...common,assets:readonlyAssets});await document.fonts.ready;
    window.seekFrame=async F=>{
      if(!Number.isInteger(F)||F<0||F>=o.frames)throw Error('Frame outside integer [0,N)');
      // Width reset clears all state, clip, path and pixels; behavior independent of Canvas.reset availability.
      canvas.width=o.width; canvas.height=o.height;
      const env=Object.freeze({...common,outputFrame:F,sampleFrame:F,timeSeconds:F*o.fps.den/o.fps.num,assets:readonlyAssets});
      const oldRandom=Math.random,oldNow=Date.now;
      Math.random=()=>{throw Error('Unseeded random forbidden during frame drawing');};
      Date.now=()=>{throw Error('Wall clock forbidden during frame drawing');};
      try {
        const result=mod.drawFrame(ctx,env);
        if(result&&typeof result.then==='function')throw Error('drawFrame must be synchronous; prepare resources first');
      } finally {Math.random=oldRandom;Date.now=oldNow;}
      return canvas.toDataURL('image/png').split(',')[1];
    };
    window.ready=true;
    return {fonts,userAgent:navigator.userAgent};
  },{config,timeline,base}));
  const reportPath=path.join(review,'render.json');
  let previous=null;
  try{previous=await read(reportPath);}catch(e){if(e.code!=='ENOENT')throw e;}
  const tools={};for(const f of await fs.readdir(here)) if(/\.(py|mjs)$/.test(f))tools[f]=await digest(path.join(here,f));
  if(previous&&previous.browser!==browser.version())throw Error('Browser version changed; create a new revision');
  if(previous&&(previous.fingerprint!==manifest.fingerprint||JSON.stringify(previous.toolHashes)!==JSON.stringify(tools)))throw Error('Render dependencies changed; create a new revision');
  const hashes={...(previous?.frames||{})},start=performance.now();
  for(const F of frames) {
    const data=Buffer.from(await timed(page.evaluate(F=>window.seekFrame(F),F)),'base64');
    const d=hash(data);
    if(hashes[F]&&hashes[F]!==d)throw Error('Existing frame differs: '+F);
    hashes[F]=d;
    // Write only once after checking any preexisting frame. Never replace reviewed pixels.
    const target=path.join(frameDir,String(F).padStart(6,'0')+'.png');
    try{await fs.writeFile(target,data,{flag:'wx'});}catch(e){if(e.code!=='EEXIST'||await digest(target)!==d)throw Error('Refusing to replace frame '+F);}
  }
  for(const F of [...frames].reverse().concat(frames.slice(0,1))) {
    const d=hash(Buffer.from(await timed(page.evaluate(F=>window.seekFrame(F),F)),'base64'));
    if(d!==hashes[F])throw Error('Order-dependent or non-deterministic frame: '+F);
  }
  if(pageErrors.length||serverErrors.length)throw Error([...pageErrors,...serverErrors].join('\n'));
  // Verify inputs again after execution to detect concurrent source edits.
  for(const [rel,sha] of Object.entries(manifest.files))if(await digest(await inside(source,rel))!==sha)throw Error('Input changed during render: '+rel);
  const report={fingerprint:manifest.fingerprint,frames:hashes,width:o.width,height:o.height,fps:o.fps,determinism:'pass',determinismScope:'requested frames forward/reverse/repeat, same browser context; not a proof for all code paths',toolHashes:tools,browser:browser.version(),fonts:setup.fonts,renderer:'Canvas 2D sRGB',elapsedSeconds:(performance.now()-start)/1000,requested:frames,network:'virtual origin, all requests intercepted; allowlist only',chromiumSandbox:true};
  // Each run has immutable evidence. The full-coverage summary stays unchanged on identical retries.
  const runs=path.join(review,'render-runs');await fs.mkdir(runs,{recursive:true});
  if(previous){const oldBytes=JSON.stringify(previous,null,2)+'\n';try{await fs.writeFile(path.join(runs,hash(oldBytes)+'.json'),oldBytes,{flag:'wx'});}catch(e){if(e.code!=='EEXIST')throw e;}}
  const runBytes=JSON.stringify(report,null,2)+'\n';await fs.writeFile(path.join(runs,hash(runBytes)+'.json'),runBytes,{flag:'wx'});
  if(!previous||JSON.stringify(previous.frames)!==JSON.stringify(hashes)){
    const temp=reportPath+'.tmp';await fs.writeFile(temp,runBytes,{flag:'wx'});await fs.rename(temp,reportPath);
  }
  if(command==='compare') {
    const result=spawnSync(opts.python||process.env.RM_PYTHON||'python3',[path.join(here,'sync.py'),'--project',project,'--revision',opts.revision,'--kind','reference','--frames',frames.join(',')],{encoding:'utf8'});
    if(result.status!==0)throw Error(result.stderr||'Comparison failed');console.log(result.stdout.trim());
  }
  console.log(JSON.stringify({frames:frames.length,output:frameDir,report:reportPath,determinism:'pass'}));
}
try{await main();}catch(e){console.error('ERROR: '+e.stack);process.exitCode=1;}finally{
  await context?.close().catch(()=>{});await browser?.close().catch(()=>{});
  if(lock){await lock.close();await fs.unlink(lock.pathName).catch(()=>{});}
}
