#!/usr/bin/env python3
"""Project identities, immutable snapshots, feedback and scoped plain-file memory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from _common import (VERSION, atomic_json, cli_main, file_lock, ident, load_revision,
                     now, read_json, safe_path, sha256, tool_hashes, validate_project)


def validate_workspace_paths(w):
    for rel in ('memory', 'memory/preferences.json', 'memory/capabilities.json', 'memory/tombstones.json', 'memory/forget-transaction'):
        safe_path(w, rel)


def validate_working_paths(p):
    for rel in ('project.json', 'timeline.json', 'BRIEF.md', 'SPEC.md', 'REPORT.md', 'used-memory.json', 'src', 'audio', 'history', 'history/feedback.jsonl', 'revisions', 'out'):
        safe_path(p, rel)
    for name in ('src', 'audio', 'history'):
        if (p / name).exists():
            for f in (p / name).rglob('*'):
                safe_path(p, f.relative_to(p))


def workspace_for(project, config):
    expected = Path(project).resolve().parent.parent
    configured = (Path(project) / config['learning']['workspace']).resolve()
    if configured != expected:
        raise ValueError('Learning workspace must be this project workspace; cross-workspace reads denied')
    validate_workspace_paths(expected)
    return expected


def evidence(root, items, hashed=False):
    if not items:
        raise ValueError('Evidence required')
    for item in items:
        rel = item if isinstance(item, str) else item['path']
        p = safe_path(root, rel)
        if not p.is_file():
            raise ValueError('Missing evidence: ' + rel)
        if hashed and (not isinstance(item, dict) or item.get('sha256') != sha256(p)):
            raise ValueError('Evidence hash mismatch: ' + rel)


def validate_feedback(project, records):
    ids = set()
    for r in records:
        ident(r['id'])
        if r['id'] in ids:
            raise ValueError('Duplicate feedback ID')
        ids.add(r['id'])
        if r['projectId'] != read_json(project / 'project.json')['id']:
            raise ValueError('Feedback belongs to another project')
        if r.get('corrects'):
            if r['corrects'] not in ids - {r['id']}:
                raise ValueError('Correction must reference earlier feedback')
        elif not r.get('quote') or not r.get('source') or not r.get('scope'):
            raise ValueError('Feedback needs verbatim quote, source and scope')
        rev = r.get('reviewedRevision')
        if rev is not None:
            load_revision(project, rev)
            if not (project / 'out' / rev / 'preview.mp4').is_file():
                raise ValueError('Reviewed revision has no reviewable preview')
        if r.get('resultingRevision'):
            load_revision(project, r['resultingRevision'])
        if r.get('userOutcome', 'unknown') != 'unknown' and not r.get('userOutcomeSource'):
            raise ValueError('User outcome requires separate real source')
        if r.get('evidence'):
            evidence(project, r['evidence'])


def feedback_records(project):
    p = project / 'history/feedback.jsonl'
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def validate_memory(workspace, kind, records):
    validate_workspace_paths(workspace)
    if not isinstance(records, list):
        raise ValueError('Memory is an array')
    tombs = read_json(workspace / 'memory/tombstones.json') if (workspace / 'memory/tombstones.json').exists() else []
    deleted = {t['id'] for t in tombs if t.get('deleted')}
    ids = set()
    for r in records:
        ident(r['id'])
        if r['id'] in ids or r['id'] in deleted:
            raise ValueError('Duplicate or deleted memory ID: ' + r['id'])
        ids.add(r['id'])
        if kind == 'preferences':
            if r['status'] not in ('candidate', 'confirmed', 'superseded', 'retracted'):
                raise ValueError('Invalid preference status')
            for field in ('category', 'rule', 'scope', 'appliesWhen', 'sources', 'updatedAt'):
                if not r.get(field):
                    raise ValueError('Preference needs ' + field)
            scope = r['scope']
            if scope['type'] not in ('project', 'brand', 'task', 'workspace') or not scope.get('id'):
                raise ValueError('Explicit preference scope required')
            if scope['type'] in ('brand', 'task') and not scope.get('client'):
                raise ValueError('Brand/task reuse needs explicit client scope')
            for src in r['sources']:
                p = safe_path(workspace, src['path'])
                if p.name == 'feedback.jsonl':
                    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
                    row = next((x for x in rows if x['id'] == src['feedbackId']), None)
                    if row is None or row.get('status') == 'retracted' or any(x.get('corrects') == src['feedbackId'] and x.get('status') == 'retracted' for x in rows):
                        raise ValueError('Preference source missing or retracted')
                    if r['status'] == 'confirmed' and not src.get('explicitConfirmation'):
                        raise ValueError('Confirmed preference needs explicit scoped user confirmation')
                else:
                    evidence(workspace, [src], hashed=True)
                    if r['status'] == 'confirmed' and not src.get('explicitConfirmation'):
                        raise ValueError('Confirmed preference requires explicit user instruction evidence')
        else:
            if r['status'] not in ('proposed', 'verified_in_scope', 'failed', 'stale'):
                raise ValueError('Invalid capability status')
            for field in ('task', 'method', 'conditions', 'limits'):
                if not r.get(field):
                    raise ValueError('Capability needs ' + field)
            if r['status'] == 'verified_in_scope':
                if not all(r.get(x) for x in ('environment', 'codeRef', 'lastTested', 'checkMethod')):
                    raise ValueError('Verified capability needs environment, code, date and check method')
                evidence(workspace, r['evidence'], hashed=True)
                evidence(workspace, [r['codeRef']], hashed=True)
                if any(e.get('kind') not in ('test', 'measurement', 'render') for e in r['evidence']):
                    raise ValueError('Preference/approval is not capability evidence')


def active_memory(workspace, config):
    validate_workspace_paths(workspace)
    if not config['learning'].get('enabled', True):
        return {'active': [], 'ignored': [{'reason': 'learning disabled'}]}
    prefs = read_json(workspace / 'memory/preferences.json')
    caps = read_json(workspace / 'memory/capabilities.json')
    tombs = read_json(workspace / 'memory/tombstones.json') if (workspace / 'memory/tombstones.json').exists() else []
    blocked = {r['id'] for r in tombs} | {r['id'] for r in prefs if r['status'] in ('retracted', 'superseded')}
    changed = True
    while changed:
        previous = len(blocked)
        blocked |= {r['id'] for r in prefs + caps if set(r.get('derivedFrom', [])) & blocked}
        changed = len(blocked) != previous
    result = {'active': [], 'ignored': []}
    for kind, rows in [('preferences', prefs), ('capabilities', caps)]:
        for r in rows:
            reason = None
            if r['id'] in blocked:
                reason = 'retracted/deleted source or derived record'
            elif kind == 'preferences':
                s = r['scope']
                match = ((s['type'] == 'project' and s['id'] == config['id']) or
                         (s['type'] == 'workspace' and s['id'] == workspace.name) or
                         (s['type'] in ('brand', 'task') and config.get(s['type']) == s['id'] and config.get('client') == s.get('client')))
                if not match:
                    reason = 'scope mismatch'
                elif r['status'] != 'confirmed':
                    reason = 'candidate is not a confirmed requirement'
                elif r['id'] in config.get('memoryOverrides', []):
                    reason = 'overridden by current instruction'
                elif config['mode'] == 'match' and not r.get('allowedOutsideLocks'):
                    reason = 'match locked constraints take priority; applicability must be explicit'
            elif r['status'] != 'verified_in_scope':
                reason = r['status']
            try:
                if reason is None:
                    validate_memory(workspace, kind, [r])
                    if kind == 'capabilities' and r['environment'].get('toolHashes') != tool_hashes():
                        reason = 'stale: tool environment changed; retest required'
            except (ValueError, OSError, KeyError) as exc:
                reason = 'stale source/evidence: ' + str(exc)
            if reason:
                result['ignored'].append({'id': r['id'], 'reason': reason})
            else:
                result['active'].append({'kind': kind, **r})
    return result


def init(args):
    w = Path(args.workspace).resolve()
    validate_workspace_paths(w)
    p = safe_path(w, 'projects/' + ident(args.id))
    if args.mode == 'match' and not args.ref:
        raise ValueError('match requires --ref')
    ref = None
    if args.ref:
        refpath = Path(args.ref).resolve(strict=True)
        ref = {'path': str(refpath), 'sha256': sha256(refpath)}
    p.mkdir(parents=True, exist_ok=False)
    config = {'schemaVersion': 1, 'id': args.id, 'revision': None, 'parentRevision': None,
              'mode': args.mode, 'reference': ref, 'output': None, 'renderEntry': None,
              'scoreEntry': None, 'assets': [], 'swaps': [], 'locks': [], 'seed': 0,
              'audio': {'source': 'procedural', 'sampleRate': 48000, 'channels': 2,
                        'narrationRequired': False, 'loudnessTarget': -14, 'truePeakMax': -1},
              'learning': {'enabled': True, 'workspace': '../..'},
              'permissions': {'upload': False, 'networkAssets': False, 'paidServices': False}}
    atomic_json(p / 'project.json', config)
    (p / 'history').mkdir()
    (p / 'history/feedback.jsonl').touch()
    for kind in ('preferences', 'capabilities'):
        target = w / 'memory' / (kind + '.json')
        if not target.exists():
            try:
                atomic_json(target, [], expected_hash='missing')
            except ValueError:
                if not target.exists():
                    raise
    print(p)


def snapshot(args):
    p = Path(args.project).resolve()
    validate_working_paths(p)
    c, t = validate_project(p)
    rev = ident(args.revision)
    dest = safe_path(p, 'revisions/' + rev)
    if dest.exists():
        raise ValueError('Snapshot exists; choose a new revision')
    w = workspace_for(p, c)
    selected = active_memory(w, c)
    used = read_json(p / 'used-memory.json') if (p / 'used-memory.json').exists() else []
    active = {r['id']: r for r in selected['active']}
    for u in used:
        if u['id'] not in active or not u.get('purpose') or u.get('recordHash') != hashlib.sha256(json.dumps(active[u['id']], sort_keys=True).encode()).hexdigest():
            raise ValueError('Used memory inactive, changed or missing purpose/hash; reselect current records')
    for name in ('BRIEF.md', 'SPEC.md'):
        if not (p / name).is_file() or not (p / name).read_text().strip():
            raise ValueError('Author ' + name + ' before snapshot')
    dest.parent.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='.snapshot-', dir=dest.parent))
    source = tmp / 'source'
    source.mkdir()
    old_config_hash = sha256(p / 'project.json')
    frozen = dict(c, revision=rev, parentRevision=c.get('revision'))
    try:
        for name in ('BRIEF.md', 'SPEC.md', 'REPORT.md', 'timeline.json'):
            if (p / name).exists():
                shutil.copy2(safe_path(p, name), source / name)
        for dirname in ('src', 'audio'):
            base = p / dirname
            if base.exists():
                for item in base.rglob('*'):
                    if item.is_file() and '__pycache__' not in item.parts:
                        src = safe_path(p, str(item.relative_to(p)))
                        dst = source / item.relative_to(p)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
        for asset in c.get('assets', []):
            # Analysis assets never enter the renderer/score snapshot.
            if asset['use'] == 'production':
                src = safe_path(p, asset['path'])
                dst = safe_path(source, asset['path'])
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        frozen['assets'] = [a for a in c.get('assets', []) if a['use'] == 'production']
        atomic_json(source / 'project.json', frozen)
        files = {str(f.relative_to(source)): sha256(f) for f in sorted(source.rglob('*')) if f.is_file()}
        hashes = tool_hashes()
        fingerprint = hashlib.sha256(json.dumps({'files': files, 'tools': hashes}, sort_keys=True).encode()).hexdigest()
        atomic_json(tmp / 'manifest.json', {'schemaVersion': 1, 'projectId': c['id'], 'revision': rev,
                    'parentRevision': c.get('revision'), 'createdAt': now(), 'files': files,
                    'fingerprint': fingerprint, 'toolVersion': VERSION, 'toolHashes': hashes,
                    'hostModel': 'unknown', 'reference': c.get('reference')})
        atomic_json(tmp / 'used-memory.json', {'used': used, 'selection': selected, 'policy': 'history only; never reactivates memory'})
        with file_lock(p / 'snapshot-transaction'):
            if sha256(p / 'project.json') != old_config_hash or dest.exists():
                raise ValueError('Project changed during snapshot')
            os.rename(tmp, dest)
            atomic_json(p / 'project.json', dict(c, revision=rev, parentRevision=c.get('revision')), expected_hash=old_config_hash)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    print(dest)


def restore(args):
    p = Path(args.project).resolve()
    validate_working_paths(p)
    if safe_path(p, 'revisions/' + ident(args.revision)).exists():
        raise ValueError('Restore target revision exists; working copy unchanged')
    source, c, t, m = load_revision(p, args.from_revision)
    # Preserve current authored files before changing the working copy, including unsnapshotted edits.
    backup = p / 'history' / ('working-before-restore-' + ident(args.revision))
    backup.mkdir(exist_ok=False)
    for name in ('src', 'audio', 'BRIEF.md', 'SPEC.md', 'REPORT.md', 'timeline.json', 'project.json', 'used-memory.json'):
        f = p / name
        if f.is_dir():
            shutil.copytree(f, backup / name)
        elif f.is_file():
            shutil.copy2(f, backup / name)
    for name in ('src', 'audio'):
        if (p / name).exists():
            shutil.rmtree(p / name)
        if (source / name).exists():
            shutil.copytree(source / name, p / name)
    for name in ('BRIEF.md', 'SPEC.md', 'timeline.json'):
        shutil.copy2(source / name, p / name)
    for asset in c.get('assets', []):
        src = safe_path(source, asset['path'])
        dst = safe_path(p, asset['path'])
        if dst.exists():
            saved = safe_path(backup, asset['path'])
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, saved)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    atomic_json(p / 'project.json', dict(c, revision=args.from_revision))
    atomic_json(p / 'used-memory.json', [])
    snapshot(argparse.Namespace(project=str(p), revision=args.revision))
    print('Working copy preserved at ' + str(backup))


def check(args):
    p = Path(args.project).resolve()
    validate_working_paths(p)
    c, t = validate_project(p, 'init' if args.stage == 'init' else 'render')
    w = workspace_for(p, c)
    validate_feedback(p, feedback_records(p))
    for kind in ('preferences', 'capabilities'):
        validate_memory(w, kind, read_json(w / 'memory' / (kind + '.json')))
    if c.get('revision'):
        load_revision(p, c['revision'])
    print(json.dumps({'status': 'pending' if t is None else 'pass', 'stage': args.stage,
                      'project': c['id'], 'memory': active_memory(w, c)}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    q = sub.add_parser('init'); q.add_argument('--workspace', required=True); q.add_argument('--id', required=True)
    q.add_argument('--mode', choices=['match', 'create'], required=True); q.add_argument('--ref'); q.set_defaults(func=init)
    q = sub.add_parser('check'); q.add_argument('--project', required=True); q.add_argument('--stage', choices=['init', 'render'], default='init'); q.set_defaults(func=check)
    q = sub.add_parser('snapshot'); q.add_argument('--project', required=True); q.add_argument('--revision', required=True); q.set_defaults(func=snapshot)
    q = sub.add_parser('restore'); q.add_argument('--project', required=True); q.add_argument('--from', dest='from_revision', required=True); q.add_argument('--revision', required=True); q.set_defaults(func=restore)
    q = sub.add_parser('feedback'); q.add_argument('--project', required=True); q.add_argument('--record', required=True)
    q = sub.add_parser('memory'); q.add_argument('--project', required=True)
    q = sub.add_parser('memory-write'); q.add_argument('--workspace', required=True); q.add_argument('--kind', choices=['preferences', 'capabilities'], required=True); q.add_argument('--input', required=True); q.add_argument('--expected-hash', required=True)
    q = sub.add_parser('forget'); q.add_argument('--workspace', required=True); q.add_argument('--id', required=True); q.add_argument('--delete', action='store_true')
    a = parser.parse_args()
    if hasattr(a, 'func'):
        a.func(a)
    elif a.command == 'feedback':
        p = Path(a.project).resolve(); validate_working_paths(p); target = p / 'history/feedback.jsonl'
        row = read_json(a.record)
        with file_lock(target):
            rows = feedback_records(p) + [row]
            validate_feedback(p, rows)
            # append is kept under lock; never edits previous user words.
            with target.open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n'); f.flush(); os.fsync(f.fileno())
        print(target)
    elif a.command == 'memory':
        p = Path(a.project).resolve(); c = read_json(p / 'project.json')
        result = active_memory(workspace_for(p, c), c)
        for r in result['active']:
            r['recordHash'] = hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif a.command == 'memory-write':
        w = Path(a.workspace).resolve(); rows = read_json(a.input)
        validate_memory(w, a.kind, rows)
        atomic_json(w / 'memory' / (a.kind + '.json'), rows, expected_hash=a.expected_hash)
        print('Memory updated with compare-and-swap')
    elif a.command == 'forget':
        w = Path(a.workspace).resolve(); validate_workspace_paths(w); ident(a.id)
        with file_lock(w / 'memory/forget-transaction'):
            all_rows = read_json(w / 'memory/preferences.json') + read_json(w / 'memory/capabilities.json')
            removed = {a.id}
            while True:
                nxt = removed | {r['id'] for r in all_rows if set(r.get('derivedFrom', [])) & removed}
                if nxt == removed: break
                removed = nxt
            # Tombstones precede cleanup; upgrade retraction to deletion when requested.
            tpath = w / 'memory/tombstones.json'
            tombs = read_json(tpath) if tpath.exists() else []
            by_id = {t['id']: t for t in tombs}
            for key in removed:
                by_id[key] = {'id': key, 'at': now(), 'deleted': a.delete or by_id.get(key, {}).get('deleted', False)}
            atomic_json(tpath, list(by_id.values()))
            for kind in ('preferences', 'capabilities'):
                path = w / 'memory' / (kind + '.json'); h = sha256(path); rows = read_json(path)
                updated = []
                for r in rows:
                    if r['id'] in removed:
                        if a.delete: continue
                        r['status'] = 'retracted' if kind == 'preferences' else 'stale'
                    updated.append(r)
                atomic_json(path, updated, expected_hash=h)
            if a.delete:
                # Scrub only copied learning records; user quotes/source and prior films retain separate scope.
                for path in list((w / 'projects').glob('*/revisions/*/used-memory.json')) + list((w / 'projects').glob('*/used-memory.json')) + list((w / 'projects').glob('*/history/working-before-restore-*/used-memory.json')):
                    safe_path(w, path.relative_to(w))
                    value = read_json(path)
                    if isinstance(value, list):
                        atomic_json(path, [x for x in value if x.get('id') not in removed])
                    if isinstance(value, dict):
                        for key in ('used',):
                            value[key] = [x for x in value.get(key, []) if x.get('id') not in removed]
                        sel = value.get('selection', {})
                        sel['active'] = [x for x in sel.get('active', []) if x.get('id') not in removed]
                        atomic_json(path, value)
        print('Inactive for future use; historical movies and separately scoped source feedback retained')

if __name__ == '__main__':
    cli_main(main)
