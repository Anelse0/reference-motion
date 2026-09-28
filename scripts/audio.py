#!/usr/bin/env python3
"""Procedural audio primitives, measured cues, normalization and reference analysis.

Project code supplies compose(synth, timeline, spec) -> stems or (stems, synth.events).
There is no built-in score. The import/input audit is NOT a security sandbox.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave

import numpy as np
from _common import (cli_main, duration_samples, load_revision, probe, read_json,
                     revision_output, run, safe_path, seconds_for_event, sha256)


def immutable_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Output changed; create a new revision: {path}")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, path)  # Atomic publication, never overwrite an existing revision artifact.
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError(f'Concurrent output conflict; create a new revision: {path}')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def immutable_json(path, data):
    immutable_bytes(path, (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n').encode())


def immutable_copy(source, destination):
    immutable_bytes(destination, Path(source).read_bytes())


class Synth:
    """Stateless signal primitives plus explicit event placement; no orchestration."""
    def __init__(self, sample_rate, channels, duration_samples, seed=0):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.duration_samples = int(duration_samples)
        self.seed = int(seed)
        self.events = []

    def track(self):
        return np.zeros((self.duration_samples, self.channels), dtype=np.float64)

    def oscillator(self, freq, samples=None, wave='sine', phase=0.0):
        frequencies = np.asarray(freq, dtype=float)
        if frequencies.ndim == 0:
            if samples is None:
                raise ValueError('Scalar frequency requires samples')
            frequencies = np.full(int(samples), float(freq))
        elif frequencies.ndim != 1 or (samples is not None and len(frequencies) != samples):
            raise ValueError('Frequency must be scalar or a samples-long array')
        if not np.isfinite(frequencies).all() or np.any(frequencies <= 0) or np.any(frequencies >= self.sample_rate / 2):
            raise ValueError('Oscillator frequencies must be finite and below Nyquist')
        # The phase at sample zero is exactly the supplied phase.
        phases = float(phase) + 2 * np.pi * np.r_[0, np.cumsum(frequencies[:-1])] / self.sample_rate
        if wave == 'sine':
            return np.sin(phases)
        if wave not in ('square', 'saw', 'triangle'):
            raise ValueError('wave must be sine, square, saw or triangle')
        # Additive band limiting; very low frequencies cap at 256 harmonics.
        harmonics = min(256, int((self.sample_rate / 2 - 1) / np.max(frequencies)))
        result = np.zeros(len(frequencies))
        for harmonic in range(1, harmonics + 1):
            if wave == 'square' and harmonic % 2:
                result += 4 / np.pi / harmonic * np.sin(harmonic * phases)
            elif wave == 'saw':
                result += 2 / np.pi * (-1) ** (harmonic + 1) / harmonic * np.sin(harmonic * phases)
            elif wave == 'triangle' and harmonic % 2:
                result += 8 / np.pi ** 2 * (-1) ** ((harmonic - 1) // 2) / harmonic ** 2 * np.sin(harmonic * phases)
        return result

    def noise(self, samples, event_id):
        seed_bytes = hashlib.sha256(f'{self.seed}:{event_id}'.encode()).digest()[:8]
        return np.random.default_rng(int.from_bytes(seed_bytes, 'little')).uniform(-1, 1, int(samples))

    def envelope(self, samples, attack=0.005, release=0.05):
        samples = int(samples)
        a, r = round(attack * self.sample_rate), round(release * self.sample_rate)
        if min(samples, a, r) < 0 or a + r > samples:
            raise ValueError('Envelope attack and release must fit samples')
        result = np.ones(samples)
        if a:
            result[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
        if r:
            result[-r:] = np.sin(np.linspace(np.pi / 2, 0, r)) ** 2
        return result

    def lowpass(self, signal, cutoff):
        if not 0 < cutoff < self.sample_rate / 2:
            raise ValueError('cutoff must be below Nyquist')
        signal = np.asarray(signal, dtype=float)
        result = np.empty_like(signal)
        state = np.zeros(signal.shape[1:])
        coefficient = 1 - math.exp(-2 * math.pi * cutoff / self.sample_rate)
        for i, value in enumerate(signal):
            state = state + coefficient * (value - state)
            result[i] = state
        return result

    def pan(self, signal, pan=0.0):
        signal = np.asarray(signal, dtype=float)
        if signal.ndim != 1 or not -1 <= pan <= 1 or self.channels != 2:
            raise ValueError('pan needs mono input, stereo output and pan in [-1,1]')
        angle = (pan + 1) * math.pi / 4
        return np.column_stack((signal * math.cos(angle), signal * math.sin(angle)))

    def delay(self, signal, delay_samples, feedback=0.3, repeats=3):
        signal = np.asarray(signal, dtype=float)
        if delay_samples < 0 or int(delay_samples) != delay_samples or repeats < 0 or int(repeats) != repeats or abs(feedback) >= 1:
            raise ValueError('delay needs nonnegative integer delay/repeats and |feedback|<1')
        delay_samples, repeats = int(delay_samples), int(repeats)
        result = np.zeros((len(signal) + delay_samples * repeats,) + signal.shape[1:])
        for repeat in range(repeats + 1):
            start = delay_samples * repeat
            result[start:start + len(signal)] += signal * feedback ** repeat
        return result

    def place(self, track, signal, start_sample, event_id, anchor='onset'):
        signal = np.asarray(signal, dtype=float)
        if signal.ndim == 1:
            signal = np.repeat(signal[:, None], self.channels, axis=1)
        if track.shape != (self.duration_samples, self.channels) or signal.ndim != 2 or signal.shape[1] != self.channels:
            raise ValueError('Invalid track or signal shape')
        if int(start_sample) != start_sample or start_sample < 0 or start_sample + len(signal) > len(track):
            raise ValueError('Signal including tail must fit project duration')
        if not np.isfinite(signal).all() or not len(signal):
            raise ValueError('Event signal must be nonempty and finite')
        if anchor not in ('onset', 'peak'):
            raise ValueError('anchor must be onset or peak')
        if any(event['id'] == event_id for event in self.events):
            raise ValueError(f'Duplicate placed event: {event_id}')
        level = np.max(np.abs(signal), axis=1)
        active = np.flatnonzero(level > max(1e-7, float(level.max()) * 1e-4))
        if not len(active):
            raise ValueError('A silent signal cannot supply an event anchor')
        start = int(start_sample)
        track[start:start + len(signal)] += signal
        event = {'id': str(event_id), 'startSample': start, 'endSample': start + len(signal),
                 'onsetSample': start + int(active[0]), 'lastActiveSample': start + int(active[-1]),
                 'peakSample': start + int(level.argmax()), 'peak': float(level.max()),
                 'anchor': anchor, 'signalSha256': hashlib.sha256(signal.astype('<f8').tobytes()).hexdigest()}
        self.events.append(event)
        return event


def write_wav(path, pcm, sample_rate):
    """Write float PCM through FFmpeg (no silent clipping of stems)."""
    pcm = np.asarray(pcm, dtype='<f4')
    if pcm.ndim != 2 or not np.isfinite(pcm).all():
        raise ValueError('PCM must be finite samples x channels')
    command = ['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(sample_rate), '-ac', str(pcm.shape[1]),
               '-i', 'pipe:0', '-c:a', 'pcm_f32le', str(path)]
    subprocess.run(command, input=pcm.tobytes(), check=True, capture_output=True)


def read_pcm(path, sample_rate, channels):
    command = ['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0', '-f', 'f32le',
               '-ar', str(sample_rate), '-ac', str(channels), 'pipe:1']
    result = subprocess.run(command, check=True, capture_output=True)
    pcm = np.frombuffer(result.stdout, dtype='<f4')
    if len(pcm) % channels:
        raise ValueError('Malformed decoded PCM')
    return pcm.reshape(-1, channels).astype(float)


def loudness(path, target=-14, true_peak=-1):
    result = run(['ffmpeg', '-hide_banner', '-i', str(path), '-af',
                  f'loudnorm=I={target}:TP={true_peak}:LRA=11:print_format=json', '-f', 'null', '-'])
    match = re.findall(r'\{\s*"input_i".*?\}', result.stderr, re.S)
    if not match:
        raise ValueError('FFmpeg did not report loudness')
    raw = json.loads(match[-1])
    return {key: (float(value) if math.isfinite(float(value)) else None) if key != 'normalization_type' else value
            for key, value in raw.items()}


def normalize(source, destination, sample_rate, samples, target, true_peak):
    first = loudness(source, target, true_peak - 0.5)
    if first['input_i'] is None:
        immutable_copy(source, destination)
        return {'status': 'not_applicable', 'reason': 'silence or insufficient gated loudness', 'firstPass': first}
    if first['input_tp'] is None:
        raise ValueError('Cannot normalize without finite true peak')
    filt = (f'loudnorm=I={target}:TP={true_peak - 0.5}:LRA=11:'
            f'measured_I={first["input_i"]}:measured_TP={first["input_tp"]}:'
            f'measured_LRA={first["input_lra"]}:measured_thresh={first["input_thresh"]}:'
            f'offset={first["target_offset"]}:linear=true:print_format=json,'
            f'aresample={sample_rate},apad=whole_len={samples},atrim=end_sample={samples},asetpts=N/SR/TB')
    run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-af', filt,
         '-ar', str(sample_rate), '-c:a', 'pcm_f32le', str(destination)])
    return {'status': 'applied', 'firstPass': first, 'headroomDb': 0.5}


def signal_checks(pcm, config):
    audio = config['audio']
    sr = audio['sampleRate']
    peak = float(np.max(np.abs(pcm))) if pcm.size else 0
    jumps = float(np.max(np.abs(np.diff(pcm, axis=0)))) if len(pcm) > 1 else 0
    dc = [float(x) for x in np.mean(pcm, axis=0)]
    rms = float(np.sqrt(np.mean(pcm ** 2))) if pcm.size else 0
    tail = float(np.max(np.abs(pcm[-max(1, round(sr * .001)):]))) if pcm.size else 0
    failures = []
    if not np.isfinite(pcm).all(): failures.append('nonfinite_pcm')
    if peak >= 1: failures.append('clipping')
    if max(map(abs, dc), default=0) > audio.get('maxDcOffset', .01): failures.append('dc_offset')
    if jumps > audio.get('maxSampleJump', .5): failures.append('sample_discontinuity')
    if tail > audio.get('maxTailAmplitude', .01): failures.append('unclosed_tail')
    if peak < 1e-7 and not audio.get('allowSilence', False): failures.append('unexpected_silence')
    for interval in audio.get('silenceRanges', []):
        begin, end = interval
        a, b = round(begin * sr), round(end * sr)
        if a < 0 or b > len(pcm) or b <= a:
            raise ValueError('Invalid audio.silenceRanges seconds interval')
        if float(np.max(np.abs(pcm[a:b]))) > audio.get('silenceAmplitude', .001):
            failures.append(f'locked_silence:{begin}:{end}')
    return {'status': 'pass' if not failures else 'fail', 'failures': failures, 'samples': len(pcm),
            'samplePeak': peak, 'rms': rms, 'dcOffset': dc, 'maxSampleJump': jumps,
            'lastMillisecondPeak': tail, 'listening': 'unverified'}


def audit_score(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    allowed_imports = {'numpy', 'math'}
    forbidden_calls = {'open', 'eval', 'exec', '__import__', 'compile', 'load', 'loadtxt', 'fromfile',
                       'read', 'read_bytes', 'read_text', 'save', 'savez', 'tofile', 'system', 'popen'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(item.name.split('.')[0] not in allowed_imports for item in node.names):
                raise ValueError('Score imports are limited to numpy/math; review algorithm dependencies explicitly')
        if isinstance(node, ast.ImportFrom) and (node.level or (node.module or '').split('.')[0] not in allowed_imports):
            raise ValueError('Score imports are limited to numpy/math')
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            if name in forbidden_calls:
                raise ValueError(f'Production score contains file/dynamic operation: {name}')
    return {'status': 'pass', 'method': 'AST import and file-operation review',
            'limits': 'Defense in depth only; Python project code is trusted executable code, not sandboxed.'}


def build_cues(events, timeline, config, pcm):
    sr, fps = config['audio']['sampleRate'], config['output']['fps']
    planned = {event['id']: event for event in timeline.get('events', [])}
    tolerance = config['audio'].get('cueToleranceFrames', 1) * sr * fps['den'] / fps['num']
    if tolerance < 0 or tolerance > sr * fps['den'] / fps['num']:
        raise ValueError('Cue tolerance must be between zero and one output frame')
    cues = []
    for event in events:
        if event['id'] not in planned:
            raise ValueError(f'Placed cue lacks a timeline event: {event["id"]}')
        planned_sample = round(seconds_for_event(planned[event['id']], timeline, fps) * sr)
        measured = event[event['anchor'] + 'Sample']
        region = pcm[event['startSample']:event['endSample']]
        mix_peak = float(np.max(np.abs(region))) if len(region) else 0
        cue = dict(event, plannedSample=planned_sample, actualSample=measured,
                   deltaSamples=measured - planned_sample, toleranceSamples=tolerance,
                   mixedWindowPeak=mix_peak, status='pass' if abs(measured - planned_sample) <= tolerance and mix_peak > 1e-7 else 'fail')
        cues.append(cue)
    missing = set(config['audio'].get('requiredEvents', [])) - {cue['id'] for cue in cues}
    if missing:
        raise ValueError(f'Missing required audio events: {sorted(missing)}')
    return {'events': cues, 'status': 'pass' if all(x['status'] == 'pass' for x in cues) else 'fail',
            'measurement': 'Isolated inserted PCM onset and peak; mixed-window presence checked. Perceptual masking remains unverified.'}


def synth_project(project, revision):
    source, config, timeline, manifest = load_revision(project, revision, require_tools=True)
    output = revision_output(project, revision)
    score = safe_path(source, config['scoreEntry'])
    audit = audit_score(score)
    audio = config['audio']
    n, sr, channels = duration_samples(config), audio['sampleRate'], audio['channels']
    synth = Synth(sr, channels, n, config.get('seed', 0))
    spec = {'sample_rate': sr, 'channels': channels, 'duration_samples': n, 'seed': config.get('seed', 0),
            'fps': dict(config['output']['fps']), 'design': audio.get('design', {}), 'narration_required': audio.get('narrationRequired', False)}
    namespace = {'__name__': 'project_score'}
    exec(compile(score.read_text(), str(score), 'exec'), namespace)
    result = namespace['compose'](synth, timeline, spec)
    if isinstance(result, tuple):
        stems, events = result
        if events != synth.events:
            raise ValueError('Cue events must be generated by synth.place, not manually asserted')
    else:
        stems = result
    if not isinstance(stems, dict) or not stems:
        raise ValueError('compose must return a nonempty stem dictionary')
    mix = synth.track()
    for name, values in stems.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('Stem names must be safe identifiers')
        values = np.asarray(values, dtype=float)
        if values.shape != mix.shape or not np.isfinite(values).all():
            raise ValueError(f'Stem {name} shape/nonfinite failure: expected {mix.shape}')
        stems[name] = values
        mix += values
    cues = build_cues(synth.events, timeline, config, mix)
    if audio.get('narrationRequired') and ('narration' not in stems or np.max(np.abs(stems['narration'])) < 1e-7):
        raise ValueError('Required procedural narration missing; do not silently remove narration')
    with tempfile.TemporaryDirectory(prefix='.audio-', dir=output) as temp:
        temp = Path(temp)
        for name, values in stems.items():
            write_wav(temp / f'{name}.wav', values, sr)
        write_wav(temp / 'mix.wav', mix, sr)
        normalization = normalize(temp / 'mix.wav', temp / 'master.wav', sr, n, audio['loudnessTarget'], audio['truePeakMax'])
        master = read_pcm(temp / 'master.wav', sr, channels)
        if len(master) != n:
            raise ValueError('Normalization changed duration')
        checks = signal_checks(master, config)
        measured = loudness(temp / 'master.wav', audio['loudnessTarget'], audio['truePeakMax'])
        for name in stems:
            immutable_copy(temp / f'{name}.wav', output / 'audio' / 'stems' / f'{name}.wav')
        immutable_copy(temp / 'mix.wav', output / 'audio' / 'mix.wav')
        immutable_copy(temp / 'master.wav', output / 'audio' / 'master.wav')
    immutable_json(output / 'audio' / 'cues.json', cues)
    report = {'fingerprint': manifest['fingerprint'], 'scoreSha256': sha256(score),
              'masterSha256': sha256(output / 'audio' / 'master.wav'), 'mixSha256': sha256(output / 'audio' / 'mix.wav'),
              'stems': {name: sha256(output / 'audio' / 'stems' / f'{name}.wav') for name in stems},
              'cuesSha256': sha256(output / 'audio' / 'cues.json'), 'normalization': normalization,
              'checks': checks, 'loudness': measured, 'dependencyAudit': audit,
              'referenceAudioInput': False, 'source': 'procedural', 'toolSha256': sha256(Path(__file__)),
              'ffmpeg': run(['ffmpeg', '-version']).stdout.splitlines()[0]}
    immutable_json(output / 'review' / 'audio.json', report)
    return check_project(project, revision)


def check_project(project, revision):
    source, config, timeline, manifest = load_revision(project, revision, require_tools=True)
    output = revision_output(project, revision)
    report = read_json(output / 'review' / 'audio.json')
    if report['fingerprint'] != manifest['fingerprint'] or report['toolSha256'] != sha256(Path(__file__)):
        raise ValueError('Stale audio report')
    for key, relative in [('masterSha256', 'audio/master.wav'), ('mixSha256', 'audio/mix.wav'), ('cuesSha256', 'audio/cues.json')]:
        if report[key] != sha256(output / relative):
            raise ValueError(f'Changed audio artifact: {relative}')
    for name, digest in report['stems'].items():
        if sha256(output / 'audio' / 'stems' / f'{name}.wav') != digest:
            raise ValueError('Changed stem')
    audio = config['audio']
    pcm = read_pcm(output / 'audio' / 'master.wav', audio['sampleRate'], audio['channels'])
    checks = signal_checks(pcm, config)
    failures = checks['failures'][:]
    if len(pcm) != duration_samples(config): failures.append('duration')
    measured = loudness(output / 'audio' / 'master.wav', audio['loudnessTarget'], audio['truePeakMax'])
    if measured['input_i'] is None:
        loudness_status = 'not_applicable'
        if not audio.get('allowSilence', False): failures.append('unmeasurable_loudness')
    else:
        loudness_status = 'pass' if abs(measured['input_i'] - audio['loudnessTarget']) <= 1 and measured['input_tp'] <= audio['truePeakMax'] else 'fail'
        if loudness_status == 'fail': failures.append('loudness_or_true_peak')
    cues = read_json(output / 'audio' / 'cues.json')
    if cues['status'] != 'pass': failures.append('cue_alignment')
    result = {'status': 'pass' if not failures else 'fail', 'failures': failures,
              'fingerprint': manifest['fingerprint'], 'masterSha256': report['masterSha256'],
              'cuesSha256': report['cuesSha256'], 'loudnessStatus': loudness_status,
              'loudness': measured, 'signal': checks, 'listening': 'unverified', 'userAcceptance': 'unverified'}
    immutable_json(output / 'review' / 'audio-check.json', result)
    return result


def analyze_reference(project):
    config = read_json(project / 'project.json')
    reference = config.get('reference')
    if config.get('mode') != 'match' or not reference:
        raise ValueError('Reference audio analysis requires match and an actual reference')
    path = Path(reference['path'])
    if sha256(path) != reference['sha256']:
        raise ValueError('Reference hash changed')
    media = probe(path)
    streams = [s for s in media['streams'] if s['codec_type'] == 'audio']
    if not streams:
        result = {'status': 'not_applicable', 'referenceSha256': sha256(path), 'reason': 'No reference audio stream'}
    else:
        sr = 48000
        pcm = read_pcm(path, sr, 1)
        window = 480
        envelope = [float(np.sqrt(np.mean(pcm[a:a+window] ** 2))) for a in range(0, len(pcm), window)]
        result = {'status': 'measured', 'referenceSha256': sha256(path), 'sampleRate': sr,
                  'samples': len(pcm), 'loudness': loudness(path), 'rmsWindowSeconds': .01,
                  'rmsEnvelope': envelope, 'interpretation': 'Mechanical analysis only; beat/story/music judgments unverified',
                  'productionUse': 'Analysis JSON only; waveform is not passed to compose'}
    immutable_json(project / 'ref' / 'audio-analysis.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--revision')
    parser.add_argument('operation', choices=['analyze', 'synth', 'check'])
    args = parser.parse_args()
    project = args.project.resolve()
    if args.operation != 'analyze' and not args.revision:
        parser.error('--revision is required for synth/check')
    result = analyze_reference(project) if args.operation == 'analyze' else synth_project(project, args.revision) if args.operation == 'synth' else check_project(project, args.revision)
    print(json.dumps(result, indent=2, allow_nan=False))
    if result.get('status') == 'fail':
        raise ValueError('Audio checks failed; see report')


if __name__ == '__main__':
    cli_main(main)
