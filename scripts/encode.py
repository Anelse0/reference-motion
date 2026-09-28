#!/usr/bin/env python3
"""Encode a complete immutable revision and verify the actual MP4 before promotion."""
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image
from _common import (cli_main, duration_samples, load_revision, probe, read_json,
                     revision_output, run, safe_path, sha256)
from audio import (check_project, immutable_copy, immutable_json, loudness,
                   read_pcm, signal_checks)


DEFAULT_CHECKS = {'technical', 'content', 'motion', 'audio_numeric', 'listening'}


def verify_frames(output, config, manifest):
    report = read_json(output / 'review' / 'render.json')
    if report.get('fingerprint') != manifest['fingerprint']:
        raise ValueError('Rendered frames belong to a different source snapshot')
    if report.get('determinism') != 'pass':
        raise ValueError('Rendering determinism has not passed')
    spec = config['output']
    if (report.get('width'), report.get('height'), report.get('fps')) != (spec['width'], spec['height'], spec['fps']):
        raise ValueError('Render report output specification mismatch')
    expected = {f'{frame:06d}.png' for frame in range(spec['frames'])}
    actual = {path.name for path in (output / 'frames').iterdir() if path.is_file()}
    if actual != expected:
        raise ValueError('Frame directory must contain every frame exactly once and no unrelated files')
    hashes = report.get('frames', {})
    if set(hashes) != {str(frame) for frame in range(spec['frames'])}:
        raise ValueError('Render hash coverage is incomplete')
    for frame in range(spec['frames']):
        path = output / 'frames' / f'{frame:06d}.png'
        if sha256(path) != hashes[str(frame)]:
            raise ValueError(f'Stale or tampered frame: {frame}')
        with Image.open(path) as image:
            image.load()
            if image.size != (spec['width'], spec['height']) or image.mode not in ('RGB', 'RGBA'):
                raise ValueError(f'Invalid frame dimensions or color mode: {frame}')
    return report


def audio_alignment(source, decoded, maximum_lag):
    """Cross correlation of actual waveforms after AAC decoding, with reported scope."""
    channel = int(np.argmax(np.sum(source ** 2, axis=0)))
    mono_a, mono_b = source[:, channel], decoded[:, channel]
    active = np.flatnonzero(np.abs(mono_a) > 1e-4)
    if not len(active):
        return {'status': 'not_applicable', 'reason': 'Silent source'}
    start = max(0, int(active[0]) - maximum_lag)
    size = min(len(mono_a) - start, 48000 + 2 * maximum_lag)
    a, b = mono_a[start:start+size], mono_b[start:start+size]
    if len(a) != len(b):
        raise ValueError('Decoded effective waveform has a different duration')
    fft_size = 1 << (2 * len(a) - 1).bit_length()
    correlation = np.fft.irfft(np.fft.rfft(b, fft_size) * np.conj(np.fft.rfft(a, fft_size)), fft_size)
    lags = np.arange(-maximum_lag, maximum_lag + 1)
    scores = correlation[lags % fft_size]
    best = int(lags[int(np.argmax(scores))])
    aa, bb = (a[:len(a)-best], b[best:]) if best >= 0 else (a[-best:], b[:len(b)+best])
    similarity = float(np.dot(aa, bb) / max(1e-30, math.sqrt(float(np.dot(aa, aa) * np.dot(bb, bb)))))
    return {'status': 'pass' if abs(best) <= maximum_lag and similarity >= .9 else 'fail',
            'measuredLagSamples': best, 'correlation': similarity, 'toleranceSamples': maximum_lag,
            'scope': {'startSample': start, 'samples': len(a), 'channel': channel},
            'method': 'Actual PCM cross-correlation near first activity; individual perceptual accents require listening'}


def inspect_mp4(path, output, config, manifest):
    metadata = probe(path)
    streams = metadata['streams']
    videos = [s for s in streams if s['codec_type'] == 'video']
    audios = [s for s in streams if s['codec_type'] == 'audio']
    if len(videos) != 1 or len(audios) != 1:
        raise ValueError('Expected exactly one video and one audio stream')
    video, audio = videos[0], audios[0]
    spec, sound = config['output'], config['audio']
    fps = Fraction(spec['fps']['num'], spec['fps']['den'])
    count = json.loads(run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                            '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(path)]).stdout)
    decoded_frames = int(count['streams'][0]['nb_read_frames'])
    # Decode all packets of both streams and fail on decoder warnings/errors.
    run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
    n = duration_samples(config)
    effective_samples = Fraction(str(audio['duration_ts'])) * Fraction(audio['time_base']) * sound['sampleRate']
    decoded = read_pcm(path, sound['sampleRate'], sound['channels'])
    failures = []
    if (video['width'], video['height']) != (spec['width'], spec['height']): failures.append('dimensions')
    if video['codec_name'] != 'h264' or video['pix_fmt'] != 'yuv420p': failures.append('video_format')
    if audio['codec_name'] != 'aac' or int(audio['sample_rate']) != sound['sampleRate'] or audio['channels'] != sound['channels']: failures.append('audio_format')
    if Fraction(video['avg_frame_rate']) != fps: failures.append('fps')
    if decoded_frames != spec['frames']: failures.append('frame_count')
    if Fraction(str(video['duration_ts'])) * Fraction(video['time_base']) != Fraction(spec['frames'], 1) / fps: failures.append('video_duration')
    if abs(float(video.get('start_time', '0'))) > 1e-6 or abs(float(audio.get('start_time', '0'))) > 1 / sound['sampleRate']: failures.append('stream_start')
    if abs(effective_samples - n) > 1: failures.append('audio_effective_duration')
    if len(decoded) < n or len(decoded) - n >= 1024: failures.append('audio_decoded_padding')
    if any(video.get(key) != value for key, value in [('color_range', 'tv'), ('color_space', 'bt709'), ('color_transfer', 'bt709'), ('color_primaries', 'bt709')]): failures.append('color_metadata')
    effective = decoded[:n]
    original = read_pcm(output / 'audio' / 'master.wav', sound['sampleRate'], sound['channels'])
    allowed_lag = max(0, math.floor(sound.get('cueToleranceFrames', 1) * sound['sampleRate'] / float(fps)))
    alignment = audio_alignment(original, effective, allowed_lag)
    if alignment['status'] == 'fail': failures.append('encoded_audio_alignment')
    cue_checks = []
    for cue in read_json(output / 'audio' / 'cues.json')['events']:
        start = max(0, cue['startSample'] - allowed_lag)
        stop = min(n, cue['endSample'] + allowed_lag)
        local = audio_alignment(original[start:stop], effective[start:stop], allowed_lag)
        local['eventId'] = cue['id']
        local['windowSamples'] = [start, stop]
        # The measured synthesis anchor is transported by measured codec lag.
        # This is numerical alignment of the mixed region, not an isolated perceptual accent.
        local['plannedSample'] = cue['plannedSample']
        local['estimatedDecodedAnchorSample'] = cue['actualSample'] + local.get('measuredLagSamples', 0)
        local['anchorErrorSamples'] = local['estimatedDecodedAnchorSample'] - cue['plannedSample']
        if abs(local['anchorErrorSamples']) > cue['toleranceSamples'] or local['status'] != 'pass':
            local['status'] = 'fail'
            failures.append('encoded_cue:' + cue['id'])
        cue_checks.append(local)
    measured = loudness(path, sound['loudnessTarget'], sound['truePeakMax'])
    if measured['input_i'] is None:
        loudness_status = 'not_applicable'
        if not sound.get('allowSilence', False): failures.append('encoded_unmeasurable_loudness')
    else:
        loudness_status = 'pass' if abs(measured['input_i'] - sound['loudnessTarget']) <= 1 and measured['input_tp'] <= sound['truePeakMax'] else 'fail'
        if loudness_status != 'pass': failures.append('encoded_loudness_or_true_peak')
    signal = signal_checks(effective, config)
    failures.extend('encoded_' + failure for failure in signal['failures'])
    return {'status': 'pass' if not failures else 'fail', 'failures': failures,
            'fingerprint': manifest['fingerprint'], 'artifactSha256': sha256(path),
            'renderReportSha256': sha256(output / 'review' / 'render.json'),
            'audioReportSha256': sha256(output / 'review' / 'audio-check.json'),
            'toolSha256': sha256(Path(__file__)), 'fullDecode': 'pass',
            'decodedFrames': decoded_frames, 'expectedFrames': spec['frames'],
            'fps': spec['fps'], 'videoStream': video, 'audioStream': audio,
            'audioSamples': {'expected': n, 'effective': float(effective_samples), 'decoded': len(decoded),
                             'codecPadding': len(decoded) - n, 'analysisTrim': [0, n],
                             'policy': 'Container effective duration checked independently; codec padding excluded from signal analysis'},
            'audioAlignment': alignment, 'encodedCueAlignment': cue_checks, 'encodedLoudness': measured, 'loudnessStatus': loudness_status,
            'signal': signal, 'perceptualReview': 'unverified', 'userAcceptance': 'unverified',
            'colorConversion': 'scale to full-range BT.709-matrix YUV444p12; colorspace converts sRGB transfer to BT.709 limited YUV420p',
            'ffmpeg': run(['ffmpeg', '-version']).stdout.splitlines()[0]}


def preview(project, revision):
    source, config, timeline, manifest = load_revision(project, revision, require_tools=True)
    if config.get('renderPurpose') == 'analysis-probe':
        raise ValueError('Analysis probes cannot be encoded/promoted as production')
    output = revision_output(project, revision)
    spec, sound = config['output'], config['audio']
    if spec['width'] % 2 or spec['height'] % 2:
        raise ValueError('H.264 yuv420p requires even dimensions; specify a new revision, no automatic crop')
    if spec.get('colorPolicy') != 'srgb-to-bt709-limited':
        raise ValueError('Unsupported color policy')
    verify_frames(output, config, manifest)
    audio_result = check_project(project, revision)
    # Preview remains possible for known numerical audio failures, but reports expose them.
    with tempfile.TemporaryDirectory(prefix='.encode-', dir=output) as directory:
        temporary = Path(directory) / 'preview.mp4'
        rate = f'{spec["fps"]["num"]}/{spec["fps"]["den"]}'
        filters = ('scale=in_range=full:out_range=full:out_color_matrix=bt709,format=yuv444p12le,'
                   'colorspace=ispace=bt709:iprimaries=bt709:itrc=srgb:irange=pc:'
                   'all=bt709:range=tv:format=yuv420p')
        run(['ffmpeg', '-v', 'error', '-y', '-framerate', rate, '-start_number', '0',
             '-i', str(output / 'frames' / '%06d.png'), '-i', str(output / 'audio' / 'master.wav'),
             '-map', '0:v:0', '-map', '1:a:0', '-frames:v', str(spec['frames']),
             '-vf', filters, '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
             '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-colorspace', 'bt709',
             '-color_trc', 'bt709', '-color_primaries', 'bt709', '-fps_mode', 'passthrough',
             '-video_track_timescale', str(spec['fps']['num']), '-af',
             f'atrim=end_sample={duration_samples(config)},asetpts=N/SR/TB',
             '-c:a', 'aac', '-b:a', '192k', '-ar', str(sound['sampleRate']),
             '-ac', str(sound['channels']), '-movflags', '+faststart', '-map_metadata', '-1', str(temporary)])
        report = inspect_mp4(temporary, output, config, manifest)
        if audio_result['status'] != 'pass':
            report['failures'].append('source_audio_numeric')
            report['status'] = 'fail'
        immutable_copy(temporary, output / 'preview.mp4')
    immutable_json(output / 'review' / 'encode.json', report)
    return report


def validate_qa(project, output, config, artifact_hash):
    qa = read_json(output / 'review' / 'qa.json')
    if not isinstance(qa, list):
        raise ValueError('qa.json must be a list of check/status/evidence/note records')
    required = DEFAULT_CHECKS | set(config.get('requiredChecks', []))
    if config['mode'] == 'match': required.add('reference')
    entries = {}
    for item in qa:
        check = item.get('check')
        if check != 'user_acceptance' and (item.get('status') == 'fail' or (item.get('severity') == 'critical' and item.get('status') not in ('pass', 'not_applicable'))):
            raise ValueError(f'Unresolved failed or critical QA: {check}')
        if check in entries:
            raise ValueError(f'Duplicate QA check: {check}')
        entries[check] = item
    for check in sorted(required):
        item = entries.get(check)
        if not item or item.get('status') != 'pass' or not item.get('note'):
            raise ValueError(f'Required QA is missing/not passed: {check}')
        evidence = item.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f'Required QA has no evidence: {check}')
        artifact_bound = False
        for relative in evidence:
            path = safe_path(project, relative)
            if not path.is_file():
                raise ValueError(f'Missing QA evidence: {relative}')
            if path == output / 'review' / 'qa.json':
                raise ValueError('QA cannot cite itself')
            if path.suffix == '.json':
                document = read_json(path)
                if isinstance(document, dict) and document.get('artifactSha256') == artifact_hash:
                    if check in {'content', 'motion', 'listening', 'reference'}:
                        if document.get('status') != 'pass' or not document.get('observations') or not document.get('method') or not document.get('reviewer'):
                            raise ValueError(f'{check} evidence needs actual reviewer, method and observations')
                    artifact_bound = True
        if not artifact_bound:
            raise ValueError(f'QA evidence is not bound to the exact preview SHA256: {check}')
    return qa


def final(project, revision):
    source, config, timeline, manifest = load_revision(project, revision, require_tools=True)
    if config.get('renderPurpose') == 'analysis-probe':
        raise ValueError('Analysis probes cannot be encoded/promoted as production')
    output = revision_output(project, revision)
    path = output / 'preview.mp4'
    if not path.is_file():
        raise ValueError('Encode and review preview before requesting final')
    verify_frames(output, config, manifest)
    audio = check_project(project, revision)
    previous = read_json(output / 'review' / 'encode.json')
    if previous.get('toolSha256') != sha256(Path(__file__)) or previous.get('artifactSha256') != sha256(path):
        raise ValueError('Stale encode report or preview changed')
    checked = inspect_mp4(path, output, config, manifest)
    if previous != checked or checked['status'] != 'pass' or audio['status'] != 'pass':
        raise ValueError('Final requires current complete numerical checks to pass')
    if config['mode'] == 'match':
        from match import require_match_verification
        require_match_verification(project, revision, config, manifest, sha256(path))
    qa = validate_qa(project, output, config, sha256(path))
    immutable_copy(path, output / 'final.mp4')
    result = {'status': 'pass', 'artifactSha256': sha256(path), 'fingerprint': manifest['fingerprint'],
              'promotedWithoutReencoding': True, 'qaSha256': sha256(output / 'review' / 'qa.json'),
              'userAcceptance': 'unverified', 'note': 'Required declared QA passed; final does not imply user acceptance'}
    immutable_json(output / 'review' / 'delivery.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', action='store_true')
    mode.add_argument('--final', action='store_true')
    args = parser.parse_args()
    result = final(args.project.resolve(), args.revision) if args.final else preview(args.project.resolve(), args.revision)
    print(json.dumps(result, indent=2, allow_nan=False))
    if result['status'] != 'pass':
        raise ValueError('Preview produced with numerical failures; see encode report')


if __name__ == '__main__':
    cli_main(main)
