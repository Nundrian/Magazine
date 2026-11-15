"""Magazine result publisher. This file is copied beside the attempt descriptor."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

def pairs(items):
    obj = {}
    for key, value in items:
        if key in obj:
            raise ValueError('duplicate JSON key: ' + key)
        obj[key] = value
    return obj

def parse(raw):
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))

def main():
    parser = argparse.ArgumentParser(description='Serialize and publish this attempt result.')
    parser.add_argument('--payload-file', help='A UTF-8 JSON object with the original handover keys.')
    parser.add_argument('--note', default='')
    parser.add_argument('--file', action='append', default=[])
    parser.add_argument('--check', action='append', default=[])
    parser.add_argument('--verified', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    identity = parse((root / 'attempt.json').read_text(encoding='utf-8'))
    if not identity.get('chat_id'):
        raise ValueError('Chat identity has not been assigned; publication is unavailable.')
    if args.payload_file:
        p = Path(args.payload_file)
        if p.is_symlink() or not stat.S_ISREG(p.stat().st_mode) or p.stat().st_size > 1048576:
            raise ValueError('Payload must be a regular JSON file of at most 1 MiB.')
        payload = parse(p.read_text(encoding='utf-8'))
    else:
        payload = {'result': {'step': identity['cartridge_id'], 'verified': args.verified,
                             'files': args.file, 'checks': args.check, 'note': args.note}}
    if not isinstance(payload, dict):
        raise ValueError('The handover payload must be a JSON object.')
    document = dict(identity, schema='magazine.result.v1', payload=payload)
    raw = (json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
    if len(raw) > 1048576:
        raise ValueError('Result exceeds 1 MiB.')
    target = root / 'publish' / 'result.json'
    temp = target.with_name('.result.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o644)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        readback = target.read_bytes()
        if readback != raw or parse(readback) != document:
            raise ValueError('Result read-back failed.')
    finally:
        if temp.exists():
            temp.unlink()
    print(json.dumps({'published': str(target), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}))

if __name__ == '__main__':
    main()
