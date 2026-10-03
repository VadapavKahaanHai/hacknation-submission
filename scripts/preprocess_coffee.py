"""Offline image preparation; run --help. Originals are never changed."""
import argparse
import csv
import hashlib
import io
import json
import random
import shutil
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from scipy.fft import dctn

CLASSES = {'Rust': 1, 'Cercospora': 2, 'Phoma': 3, 'Miner': 4, 'Healthy': 5, 'unknown': 6}
ALIASES = {'rust': 'Rust', 'leaf rust': 'Rust', 'cercospora': 'Cercospora',
           'cerscospora': 'Cercospora', 'cescospora': 'Cercospora',
           'phoma': 'Phoma', 'miner': 'Miner', 'healthy': 'Healthy', 'unknown': 'unknown'}
DOIS = {'jmuben': '10.17632/t2r6rszp5c.1', 'jmuben2': '10.17632/tgv3zb82nd.1'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff', '.gif'}
FIELDS = ['source_path', 'dataset_id', 'doi', 'source_class_name', 'label_en', 'class_id',
          'role', 'status', 'reason', 'source_bytes', 'original_width', 'original_height',
          'format', 'image_id', 'file_path', 'sha256', 'phash', 'phash_variants',
          'width', 'height', 'pixel_group', 'manual_group', 'is_augmented',
          'is_field_photo', 'is_synthetic', 'is_own_photo']
PAIR_FIELDS = ['image_a', 'image_b', 'path_a', 'path_b', 'hamming_distance', 'decision']
MANIFEST_FIELDS = [f for f in FIELDS if f not in ('phash_variants', 'status', 'reason')]
MANIFEST_FIELDS += ['source_group_id', 'split']


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def write_csv(path, fields, rows):
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_media(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to((root / 'media').resolve()):
        raise ValueError('Media path escapes output directory')
    return path


def clean_image(path):
    """Full decode, orientation, fresh RGB pixels; no inherited metadata."""
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(path) as probe:
            original = {'original_width': probe.width, 'original_height': probe.height,
                        'format': probe.format}
            if getattr(probe, 'n_frames', 1) != 1:
                raise ValueError('multi_frame_image')
            probe.verify()
        with Image.open(path) as source:
            source.load()  # Do not accept truncated files.
            oriented = ImageOps.exif_transpose(source)
            if 'A' in oriented.getbands() or 'transparency' in oriented.info:
                rgba = oriented.convert('RGBA')
                rgb = Image.new('RGB', rgba.size, 'white')
                rgb.paste(rgba, mask=rgba.getchannel('A'))
            else:
                rgb = oriented.convert('RGB')
            clean = Image.frombytes('RGB', rgb.size, rgb.tobytes())
    buffer = io.BytesIO()
    clean.save(buffer, format='PNG', compress_level=6)
    return clean, buffer.getvalue(), original


def fingerprints(image):
    """64-bit DCT pHashes and exact pixel identity over all eight D4 transforms."""
    variants = [image] + [image.transpose(operation) for operation in (
        Image.Transpose.FLIP_LEFT_RIGHT, Image.Transpose.FLIP_TOP_BOTTOM,
        Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_270,
        Image.Transpose.TRANSPOSE, Image.Transpose.TRANSVERSE)]
    pixel_hashes, perceptual = [], []
    for transformed in variants:
        dimensions = f'{transformed.width}x{transformed.height}:'.encode('ascii')
        pixel_hashes.append(sha(dimensions + transformed.tobytes()))
        small = transformed.convert('L').resize((32, 32), Image.Resampling.LANCZOS)
        low = dctn(np.asarray(small, dtype=np.float64), type=2, norm='ortho')[:8, :8].ravel()
        bits = low > np.median(low[1:])
        bits[0] = False  # Ignore DC (overall brightness).
        perceptual.append(int.from_bytes(np.packbits(bits).tobytes(), 'big'))
    return min(pixel_hashes), perceptual


class HashIndex:
    """Exact Hamming-radius search with radius+1 disjoint bit buckets."""
    def __init__(self, radius):
        self.radius = radius
        boundaries = [64*i//(radius+1) for i in range(radius+2)]
        self.parts = [(a, (1 << (b-a))-1) for a, b in zip(boundaries, boundaries[1:])]
        self.buckets = defaultdict(list)
        self.hashes = {}

    def add(self, key, value):
        self.hashes[key] = value
        for part, (shift, mask) in enumerate(self.parts):
            self.buckets[part, (value >> shift) & mask].append(key)

    def query(self, variants):
        matches = {}
        for value in set(variants):
            candidates = set()
            for part, (shift, mask) in enumerate(self.parts):
                candidates.update(self.buckets.get((part, (value >> shift) & mask), ()))
            for key in candidates:
                distance = (self.hashes[key] ^ value).bit_count()
                if distance <= self.radius:
                    matches[key] = min(distance, matches.get(key, 65))
        return matches


class Groups:
    def __init__(self, keys):
        self.parents = dict.fromkeys(keys)
        self.parents.update((k, k) for k in self.parents)

    def find(self, key):
        while self.parents[key] != key:
            self.parents[key] = self.parents[self.parents[key]]
            key = self.parents[key]
        return key

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        self.parents[max(a, b)] = min(a, b)


def classify(kind, relative):
    parts = relative.parts
    folder = parts[0] if len(parts) > 1 else ''
    if kind == 'plantdoc':
        # Only official train/test class trees, not README illustrations.
        if len(parts) < 3 or parts[-3].lower() not in ('train', 'test'):
            raise ValueError('outside_plantdoc_class_tree')
        return 'plantdoc', 'unknown', parts[-2], 'ood_test'
    label = ALIASES.get(folder.lower())
    if label is None or (kind == 'jmuben' and label == 'unknown'):
        raise ValueError('unrecognized_or_missing_class_folder')
    dataset = ('jmuben' if label in ('Rust', 'Cercospora', 'Phoma') else 'jmuben2')
    return (dataset if kind == 'jmuben' else 'own_photos', label, folder,
            'train' if kind == 'jmuben' else 'test')


def prepare(args):
    output = args.output.resolve()
    roots = [('jmuben', args.jmuben), ('plantdoc', args.plantdoc), ('own', args.own_photos)]
    roots = [(kind, path.resolve()) for kind, path in roots if path is not None]
    for _, root in roots:
        if not root.is_dir():
            raise ValueError(f'Input directory does not exist: {root}')
        if output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError('Output and input directories must not overlap')
    if output.exists():
        raise ValueError('Output already exists; choose a new directory (no files are overwritten)')
    manual = {}
    if args.own_groups:
        for row in read_csv(args.own_groups):
            key = row['source_path']
            if key in manual or not row['leaf_id'].strip():
                raise ValueError('Duplicate own-photo path or empty leaf_id')
            manual[key] = 'own:' + row['leaf_id'].strip()
    output.mkdir(parents=True)
    (output / 'media').mkdir()
    counts = Counter()
    clean_rows = []
    with (output / 'inventory.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, FIELDS)
        writer.writeheader()
        for kind, root in roots:
            for path in sorted(root.rglob('*')):
                if not path.is_file():
                    continue
                relative = path.relative_to(root)
                row = dict.fromkeys(FIELDS, '')
                row.update(source_path=f'{kind}/{relative.as_posix()}', source_bytes=path.stat().st_size)
                processing_phase = 'input'
                try:
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        raise ValueError('external_or_symbolic_file')
                    if path.suffix.lower() not in IMAGE_EXTS:
                        row.update(status='ignored', reason='non_image_extension')
                    else:
                        dataset, label, folder, role = classify(kind, relative)
                        row.update(dataset_id=dataset, doi=DOIS.get(dataset, ''), label_en=label,
                                   class_id=CLASSES[label], source_class_name=folder, role=role)
                        clean, data, original = clean_image(path)
                        processing_phase = 'output'
                        digest = sha(data)
                        target = output / 'media' / f'{digest}.png'
                        if not target.exists():
                            target.write_bytes(data)
                        with Image.open(target) as saved:
                            saved.load()
                            if saved.info or saved.getexif():
                                raise RuntimeError('Metadata remains in cleaned image')
                        pixel_group, hashes = fingerprints(clean)
                        row.update(original, status='clean', image_id=digest, sha256=digest,
                                   file_path=f'media/{digest}.png', width=clean.width, height=clean.height,
                                   pixel_group=pixel_group, phash=f'{hashes[0]:016x}',
                                   phash_variants=';'.join(f'{h:016x}' for h in hashes),
                                   manual_group=manual.get(relative.as_posix(), '') if kind == 'own' else '',
                                   is_augmented=0 if kind == 'own' else '',
                                   is_field_photo=int(kind == 'own'), is_own_photo=int(kind == 'own'),
                                   is_synthetic=0)
                        clean_rows.append(row)
                except (UnidentifiedImageError, OSError, ValueError,
                        Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
                    if processing_phase != 'input':
                        raise  # Disk-full/write failures must stop the run, not reject valid images.
                    # Use only an error code here; exception messages may contain private paths.
                    row.update(status='rejected', reason=str(error) if isinstance(error, ValueError)
                               else type(error).__name__)
                writer.writerow(row)
                counts[row['status']] += 1
                if sum(counts.values()) % 500 == 0:
                    print(f'Inventoried {sum(counts.values())} files: {dict(counts)}', flush=True)
    inventory = read_csv(output / 'inventory.csv')
    write_csv(output / 'rejected_files.csv', FIELDS, (r for r in inventory if r['status']=='rejected'))
    breakdown = Counter((r['dataset_id'], r['label_en'], r['status']) for r in inventory)
    write_csv(output / 'inventory_counts.csv', ['dataset_id','label_en','status','file_count'],
              [dict(dataset_id=d,label_en=c,status=s,file_count=n)
               for (d,c,s),n in sorted(breakdown.items())])
    dimensions = Counter((r['dataset_id'],r['original_width'],r['original_height'])
                         for r in inventory if r['status']=='clean')
    write_csv(output / 'dimension_counts.csv', ['dataset_id','width','height','file_count'],
              [dict(dataset_id=d,width=w,height=h,file_count=n)
               for (d,w,h),n in sorted(dimensions.items())])
    if manual:
        seen = {r['source_path'][4:] for r in inventory if r['source_path'].startswith('own/')}
        if set(manual) - seen:
            raise ValueError('Own-photo group manifest contains paths not present in input')
    # One search representative per exact D4 pixel group, not every augmentation.
    representatives = {}
    for row in sorted(clean_rows, key=lambda r: r['image_id']):
        representatives.setdefault(row['pixel_group'], row)
    index = HashIndex(args.radius)
    by_id = {}
    with (output / 'near_candidates.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, PAIR_FIELDS)
        writer.writeheader()
        for number, row in enumerate(representatives.values(), 1):
            variants = [int(h, 16) for h in row['phash_variants'].split(';')]
            for other_id, distance in sorted(index.query(variants).items()):
                other = by_id[other_id]
                writer.writerow(dict(image_a=other_id, image_b=row['image_id'],
                                     path_a=other['file_path'], path_b=row['file_path'],
                                     hamming_distance=distance, decision='pending'))
            index.add(row['image_id'], variants[0])
            by_id[row['image_id']] = row
            if number % 500 == 0:
                print(f'Compared {number}/{len(representatives)} pixel groups', flush=True)
    shutil.copyfile(output / 'near_candidates.csv', output / 'grouping_review.csv')
    canonical = {}
    duplicates = []
    for row in clean_rows:
        first = canonical.setdefault(row['sha256'], row['source_path'])
        if first != row['source_path']:
            duplicates.append(dict(source_path=row['source_path'], canonical_source_path=first,
                                   sha256=row['sha256'], dataset_id=row['dataset_id'], label_en=row['label_en']))
    write_csv(output / 'duplicate_map.csv',
              ['source_path','canonical_source_path','sha256','dataset_id','label_en'], duplicates)
    write_json(output / 'prepare_summary.json', {
        'complete': True, 'counts': dict(counts), 'unique_clean_files': len(canonical),
        'duplicate_aliases': len(duplicates), 'exact_d4_pixel_groups': len(representatives),
        'phash_radius': args.radius, 'common_voice': 'not read or processed',
        'own_photos_present': any(k == 'own' for k, _ in roots),
        'note': 'Pixel groups are image-identity evidence, not verified biological leaf counts.'})
    print(f'Prepared {len(canonical)} unique files in {output}. Review grouping_review.csv next.')


def split(args):
    root = args.output.resolve()
    if not (root / 'prepare_summary.json').exists():
        raise ValueError('Preparation is incomplete')
    records = [r for r in read_csv(root / 'inventory.csv') if r['status'] == 'clean']
    by_id = {r['image_id']: r for r in records}
    groups = Groups(by_id)
    for field in ('pixel_group', 'manual_group'):
        first = {}
        for row in records:
            if row[field]:
                groups.join(row['image_id'], first.setdefault(row[field], row['image_id']))
    original = read_csv(root / 'near_candidates.csv')
    reviewed = read_csv(root / 'grouping_review.csv')
    key = lambda r: (r['image_a'], r['image_b'])
    decisions = {key(r): r['decision'].strip().lower() for r in reviewed}
    if len(decisions) != len(reviewed) or set(decisions) != {key(r) for r in original}:
        raise ValueError('Review must preserve every candidate pair exactly once')
    pending, different = [], []
    for row in original:
        a, b = key(row)
        decision = decisions[a, b]
        if decision == 'same_leaf':
            groups.join(a, b)
        elif decision == 'different_leaf':
            different.append((a, b))
        elif decision in ('pending', ''):
            pending.extend((a, b))
        else:
            raise ValueError(f'Unknown review decision: {decision}')
    if args.same_leaf:
        for row in read_csv(args.same_leaf):
            groups.join(row['image_a'], row['image_b'])
    if any(groups.find(a) == groups.find(b) for a, b in different):
        raise ValueError('Contradictory review: different_leaf endpoints joined by same_leaf links')
    blocked = {groups.find(k) for k in pending}
    members = defaultdict(list)
    for row in records:
        members[groups.find(row['image_id'])].append(row)
    excluded, accepted, assignments = [], [], {}
    by_class = defaultdict(list)
    reasons = {}
    for group_id, rows in sorted(members.items()):
        if len({r['class_id'] for r in rows}) != 1:
            reasons[group_id] = 'conflicting_labels'
        elif group_id in blocked:
            reasons[group_id] = 'pending_near_duplicate_review'
        elif any(r['role'] in ('test', 'ood_test') for r in rows):
            assignments[group_id] = 'test'
        else:
            by_class[rows[0]['class_id']].append(group_id)
    rng = random.Random(args.seed)
    for class_id in sorted(by_class):
        keys = by_class[class_id]
        rng.shuffle(keys)
        n = len(keys)
        # Ratios count groups, not augmented files; require >=3 for three partitions.
        n_val = max(1, round(n * 0.15)) if n >= 3 else 0
        n_test = max(1, round(n * 0.15)) if n >= 3 else 0
        for i, group_id in enumerate(keys):
            assignments[group_id] = 'val' if i < n_val else 'test' if i < n_val+n_test else 'train'
    for group_id, rows in sorted(members.items()):
        if group_id in reasons:
            excluded.extend(dict(r, reason=reasons[group_id]) for r in rows)
            continue
        # Keep test provenance if exactly the same stored file occurs in multiple datasets.
        ordered = sorted(rows, key=lambda r: (r['role']=='train', r['dataset_id'], r['source_path']))
        unique = {}
        for row in ordered:
            unique.setdefault(row['image_id'], row)
        for row in unique.values():
            accepted.append(dict(row, source_group_id='leaf-'+group_id, split=assignments[group_id]))
    destination = root / args.name
    if destination.parent != root or destination.exists():
        raise ValueError('Split name must be a new, direct child directory')
    destination.mkdir()
    write_csv(destination / 'images_manifest.csv', MANIFEST_FIELDS, accepted)
    write_csv(destination / 'excluded_from_splits.csv', FIELDS, excluded)
    counts = Counter((r['dataset_id'], r['label_en'], r['split']) for r in accepted)
    group_counts = defaultdict(set)
    for row in accepted:
        group_counts[row['dataset_id'], row['label_en'], row['split']].add(row['source_group_id'])
    write_csv(destination / 'counts.csv', ['dataset_id','label_en','split','image_count','source_group_count'],
              [dict(dataset_id=d,label_en=c,split=s,image_count=count,
                    source_group_count=len(group_counts[d,c,s])) for (d,c,s),count in sorted(counts.items())])
    write_json(destination / 'split_summary.json', {
        'seed': args.seed, 'target_group_ratios': {'train': 0.7, 'val': 0.15, 'test': 0.15},
        'retained_files': len(accepted), 'retained_groups': len(assignments),
        'excluded_source_records': len(excluded), 'excluded_groups_by_reason': dict(Counter(reasons.values())),
        'classes_with_fewer_than_three_train_eligible_groups': [c for c, g in by_class.items() if len(g)<3],
        'review_sha256': sha((root / 'grouping_review.csv').read_bytes()),
        'inventory_sha256': sha((root / 'inventory.csv').read_bytes()),
        'manifest_sha256': sha((destination / 'images_manifest.csv').read_bytes()),
        'same_leaf_sha256': sha(args.same_leaf.read_bytes()) if args.same_leaf else None,
        'warning': 'Near-match detection is heuristic. Audit groups before claiming leakage-free evaluation.'})
    if getattr(args, 'verify_files', True):
        verify(root, destination / 'images_manifest.csv')
    print(f'Wrote {len(accepted)} unique images; excluded {len(excluded)} source records: {destination}')


def verify(root, manifest):
    rows = read_csv(manifest)
    hashes, groups = set(), {}
    for number, row in enumerate(rows, 1):
        if row['sha256'] in hashes:
            raise ValueError('Duplicate SHA-256 in manifest')
        hashes.add(row['sha256'])
        previous = groups.setdefault(row['source_group_id'], row['split'])
        if previous != row['split']:
            raise ValueError('Source group spans splits')
        if row['split'] not in ('train','val','test') or row['role'] not in ('train','test','ood_test'):
            raise ValueError('Invalid role or split')
        if row['role'] != 'train' and row['split'] != 'test':
            raise ValueError('Held-out dataset outside test split')
        if row['dataset_id'] in DOIS and row['doi'] != DOIS[row['dataset_id']]:
            raise ValueError('Incorrect DOI')
        path = checked_media(root, row['file_path'])
        if sha(path.read_bytes()) != row['sha256']:
            raise ValueError('Stored image hash mismatch')
        with Image.open(path) as image:
            image.load()
            if image.mode != 'RGB' or image.info or image.getexif():
                raise ValueError('Unexpected mode or retained metadata')
            if image.size != (int(row['width']), int(row['height'])):
                raise ValueError('Stored dimensions mismatch')
        if number % 500 == 0:
            print(f'Verified {number}/{len(rows)} files', flush=True)
    print(f'PASS: {len(rows)} files verified; {len(groups)} groups have consistent splits.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare', help='Inventory, clean, deduplicate and generate review candidates')
    p.add_argument('--jmuben', type=Path, required=True)
    p.add_argument('--plantdoc', type=Path)
    p.add_argument('--own-photos', type=Path)
    p.add_argument('--own-groups', type=Path, help='CSV: source_path (relative to own root),leaf_id')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--radius', type=int, choices=range(0, 9), default=4, help='pHash Hamming radius, default 4')
    p = commands.add_parser('split', help='Apply reviews and assign whole groups to 70/15/15 partitions')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--name', default='splits', help='New output subdirectory; use another name for a rerun')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--same-leaf', type=Path, help='Optional reviewed additional edges: image_a,image_b')
    p = commands.add_parser('verify', help='Verify hashes, metadata, dimensions and split consistency')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(args)
        elif args.command == 'split':
            split(args)
        else:
            verify(args.output.resolve(), args.manifest)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
