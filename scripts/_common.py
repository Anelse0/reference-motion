"""Small file/time/media helpers shared by the seven command-line tools."""
import contextlib
import datetime
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

VERSION = '0.1.0'

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def read_json(path):
    with Path(path).open(encoding='utf-8') as f:
        return json.load(f)

def safe_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Path escapes authorized root: ' + str(relative))
    return path

@contextlib.contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + '.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('Concurrent writer or stale lock; inspect before retry: ' + str(lock))
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()

def atomic_json(path, data, expected_hash=None):
    path = Path(path)
    with file_lock(path):
        if expected_hash is not None:
            actual = sha256(path) if path.exists() else 'missing'
            if actual != expected_hash:
                raise ValueError('Version conflict: ' + str(path))
        with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False, encoding='utf-8') as f:
            tmp = Path(f.name)
            try:
                json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
                f.write('\n')
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
        os.replace(tmp, path)

def run(args):
    return subprocess.run([str(x) for x in args], check=True, capture_output=True, text=True)

def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path]).stdout)

def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', value):
        raise ValueError('Invalid identifier: ' + str(value))
    return value

def positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(name + ' must be a positive integer')

def seconds_for_event(event, timeline, fps):
    keys = [k for k in ('frame', 'seconds', 'beat') if k in event]
    if len(keys) != 1:
        raise ValueError('Event must have exactly one time anchor')
    key = keys[0]
    value = event[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError('Invalid event time')
    if key == 'frame':
        if type(value) is not int:
            raise ValueError('Event frame must be integer')
        return float(Fraction(value * fps['den'], fps['num']))
    if key == 'seconds':
        return float(value)
    grid = timeline['beats']
    if grid['bpm'] <= 0:
        raise ValueError('BPM must be positive')
    return float(grid.get('offsetSeconds', 0) + value * 60 / grid['bpm'])

def duration_samples(config):
    o = config['output']
    return round(Fraction(o['frames'] * o['fps']['den'] * config['audio']['sampleRate'], o['fps']['num']))

def validate_project(project, stage='render'):
    project = Path(project).resolve()
    c = read_json(project / 'project.json')
    ident(c['id'])
    if c['mode'] not in ('match', 'create'):
        raise ValueError('mode must be match/create')
    ref = c.get('reference')
    if c['mode'] == 'match' and not ref:
        raise ValueError('match needs an actual reference')
    if ref:
        p = Path(ref['path'])
        if not p.is_absolute():
            p = safe_path(project, ref['path'])
        if not p.is_file() or sha256(p) != ref['sha256']:
            raise ValueError('Reference missing or hash changed')
    if stage == 'init' and not c.get('output'):
        return c, None
    o = c['output']
    for k in ('width', 'height', 'frames'):
        positive_int(o[k], k)
    for k in ('num', 'den'):
        positive_int(o['fps'][k], 'fps.' + k)
    if o.get('colorPolicy') != 'srgb-to-bt709-limited':
        raise ValueError('Declare supported colorPolicy srgb-to-bt709-limited')
    for k in ('renderEntry', 'scoreEntry'):
        if not safe_path(project, c[k]).is_file():
            raise ValueError('Missing project source: ' + k)
    a = c['audio']
    if a.get('source') != 'procedural':
        raise ValueError('Production audio must be procedural')
    positive_int(a['sampleRate'], 'sampleRate')
    if a['channels'] not in (1, 2):
        raise ValueError('channels must be 1 or 2')
    asset_ids = set()
    for asset in c.get('assets', []):
        ident(asset['id'])
        if asset['id'] in asset_ids:
            raise ValueError('Duplicate asset ID')
        asset_ids.add(asset['id'])
        if asset.get('use') not in ('production', 'analysis') or not asset.get('source'):
            raise ValueError('Asset must declare use and source')
        if asset.get('type') in ('audio', 'video') and asset['use'] == 'production':
            raise ValueError('Audio/video production assets are unsupported; extract authorized static images explicitly')
        if not safe_path(project, asset['path']).is_file():
            raise ValueError('Missing asset: ' + asset['path'])
    t = read_json(project / 'timeline.json')
    if not t.get('shots'):
        raise ValueError('Timeline requires authored shot intervals')
    cursor = 0
    ids = set()
    for shot in t['shots']:
        ident(shot['id'])
        if shot['id'] in ids or type(shot['f0']) is not int or type(shot['f1']) is not int or shot['f0'] != cursor or shot['f1'] <= cursor:
            raise ValueError('Shots must uniquely cover [0,N) without gaps/overlaps')
        cursor = shot['f1']
        ids.add(shot['id'])
    if cursor != o['frames']:
        raise ValueError('Shots do not cover N frames')
    event_ids = set()
    for e in t.get('events', []):
        ident(e['id'])
        if e['id'] in event_ids or e.get('basis') not in ('user', 'measured', 'designed', 'inferred') or not e.get('source'):
            raise ValueError('Event needs unique ID, basis and source')
        event_ids.add(e['id'])
        sec = seconds_for_event(e, t, o['fps'])
        if sec < 0 or sec >= o['frames'] * o['fps']['den'] / o['fps']['num']:
            raise ValueError('Event outside [0,N)')
    for tr in t.get('transitions', []):
        if not (type(tr['f0']) is int and type(tr['f1']) is int and 0 <= tr['f0'] < tr['f1'] <= o['frames']):
            raise ValueError('Invalid transition interval')
    return c, t

def tool_hashes():
    root = Path(__file__).parent
    return {p.name: sha256(p) for p in sorted(root.iterdir()) if p.suffix in ('.py', '.mjs')}

def load_revision(project, revision, require_tools=False):
    project = Path(project).resolve()
    root = safe_path(project, 'revisions/' + ident(revision))
    m = read_json(root / 'manifest.json')
    source = root / 'source'
    if require_tools and m.get('toolHashes') != tool_hashes():
        raise ValueError('Snapshot tool version changed; use matching tool version or create a new revision')
    if m['revision'] != revision:
        raise ValueError('Revision identity mismatch')
    for rel, digest in m['files'].items():
        p = safe_path(source, rel)
        if not p.is_file() or sha256(p) != digest:
            raise ValueError('Snapshot changed: ' + rel)
    actual = {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if actual != set(m['files']):
        raise ValueError('Snapshot file set changed')
    c, t = validate_project(source)
    if c['id'] != m['projectId'] or c['revision'] != revision:
        raise ValueError('Snapshot config identity mismatch')
    return source, c, t, m

def revision_output(project, revision):
    out = safe_path(project, 'out/' + ident(revision))
    out.mkdir(parents=True, exist_ok=True)
    return out

def cli_main(fn):
    try:
        fn()
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as e:
        print('ERROR: ' + str(e), file=sys.stderr)
        if isinstance(e, subprocess.CalledProcessError):
            print((e.stderr or '')[-5000:], file=sys.stderr)
        sys.exit(1)
