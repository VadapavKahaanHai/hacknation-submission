"""Build a separate demo pack from frozen test images; never import into training."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

from class_mapping import CLASS_IDS, DISPLAY_NAMES, MESSAGES
from runtime_db import ROOT, clean_upload


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build(prepared, output):
    prepared, output = Path(prepared).resolve(), Path(output).resolve()
    if output == prepared or prepared in output.parents or output in prepared.parents:
        raise ValueError('Demo output must be separate from prepared data')
    if output.exists():
        raise ValueError('Output exists; choose a new demo directory')
    manifest = prepared/'splits/images_manifest.csv'
    summary = json.loads((manifest.parent/'split_summary.json').read_text(encoding='utf-8'))
    for path, key in ((manifest, 'manifest_sha256'),
                      (prepared/'inventory.csv', 'inventory_sha256'),
                      (prepared/'grouping_review.csv', 'review_sha256')):
        if sha(path.read_bytes()) != summary[key]:
            raise ValueError('Frozen input changed: '+path.name)
    with manifest.open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    selected = {}
    for row in sorted(rows, key=lambda r: r['image_id']):
        if row['split'] == 'test':
            selected.setdefault(int(row['class_id']), row)
    if not selected:
        raise ValueError('No retained test images')
    # Verify all selected inputs before creating output.
    for row in selected.values():
        path = (prepared/row['file_path']).resolve()
        if prepared not in path.parents or sha(path.read_bytes()) != row['sha256']:
            raise ValueError('Unsafe path or changed sample image')
        with Image.open(path) as image:
            image.load()
            if image.info or image.mode != 'RGB' or image.size != (int(row['width']), int(row['height'])):
                raise ValueError('Expected clean RGB image with recorded dimensions')
    (output/'images').mkdir(parents=True)
    (output/'edge_cases').mkdir()
    cases = []

    def save(name, data, status=201, error=None, source=None, note=''):
        (output/name).write_bytes(data)
        cases.append(dict(path=name, sha256=sha(data), expected_http_status=status,
                          expected_error=error, source=source, note=note))

    for class_id, row in sorted(selected.items()):
        save('images/'+str(class_id)+'.png', (prepared/row['file_path']).read_bytes(),
             source={key: row[key] for key in ('image_id', 'class_id', 'label_en', 'dataset_id',
                     'doi', 'source_path', 'file_path', 'source_group_id', 'split', 'sha256')},
             note='Source label, not an expected prediction; mock always returns unknown.')
    # Artificial controls exercise decoding/transport, not disease accuracy.
    base = Image.new('RGB', (48, 32), 'green')
    base.paste('white', (0, 0, 12, 16))
    import io
    def encoded(image, fmt='PNG', **options):
        buffer = io.BytesIO()
        image.save(buffer, fmt, **options)
        return buffer.getvalue()

    exif = Image.Exif()
    exif[274] = 6
    exif[270] = 'SYNTHETIC METADATA TEST ONLY'
    save('edge_cases/orientation.jpg', encoded(base, 'JPEG', exif=exif),
         note='Synthetic EXIF control: cleaned dimensions must be 32x48; metadata stripped.')
    for name, image in [('transparent', Image.new('RGBA', (32, 24), (0, 0, 0, 0))),
                        ('grayscale', base.convert('L')), ('tiny', base.resize((1, 1))),
                        ('blank', Image.new('RGB', (32, 24), 'white'))]:
        save('edge_cases/'+name+'.png', encoded(image), note='Synthetic input; no disease label.')
    first = selected[min(selected)]
    with Image.open(prepared/first['file_path']) as image:
        for name, variant in [('blurred', image.filter(ImageFilter.GaussianBlur(8))),
                              ('dark', ImageEnhance.Brightness(image).enhance(0.08)),
                              ('rotated', image.transpose(Image.Transpose.ROTATE_90))]:
            save('edge_cases/'+name+'.png', encoded(variant),
                 source={'image_id': first['image_id'], 'split': 'test',
                         'source_group_id': first['source_group_id']},
                 note='Derived demo control, never an independent evaluation sample. No quality gate yet.')
    save('edge_cases/empty.jpg', b'', 413, 'image_size_limit')
    save('edge_cases/corrupt.jpg', b'not an image', 400, 'unreadable_image')
    save('edge_cases/truncated.png', encoded(base)[:40], 400, 'unreadable_image')
    save('edge_cases/unsupported.bmp', encoded(base, 'BMP'), 415, 'unsupported_image')
    save('edge_cases/animated.png', encoded(base, save_all=True,
         append_images=[Image.new('RGB', base.size, 'red')], duration=100, loop=0),
         415, 'unsupported_image')
    # Highly compressible: tests the pixel cap without a large on-disk fixture.
    save('edge_cases/too_many_pixels.png', encoded(Image.new('RGB', (4000, 3001), 'white')),
         413, 'image_pixel_limit')
    language = dict(languages=['en', 'hi'], human_reviewed=False,
                    purpose='UI demo text only; no treatment advice or speech audio',
                    classes=[dict(class_id=i, label_en=label, en=DISPLAY_NAMES[i][0], hi=DISPLAY_NAMES[i][1])
                             for label, i in CLASS_IDS.items()], messages=MESSAGES)
    (output/'local_language.json').write_text(json.dumps(language, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    report = dict(purpose='Demo fixtures only; never train or measure accuracy on this pack',
                  manifest_sha256=summary['manifest_sha256'],
                  missing_classes=[label for label, i in CLASS_IDS.items() if i not in selected],
                  cases=cases)
    (output/'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    verify(output)
    return report


def verify(output):
    """Exercise actual upload validation for every case, including invalid fixtures."""
    output = Path(output)
    report = json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    from runtime_db import RequestError
    for case in report['cases']:
        data = (output/case['path']).read_bytes()
        if sha(data) != case['sha256']:
            raise ValueError('Fixture changed: '+case['path'])
        try:
            image, normalized = clean_upload(data)
        except RequestError as error:
            if (error.status, error.code) != (case['expected_http_status'], case['expected_error']):
                raise AssertionError((case['path'], error.code))
        else:
            if case['expected_http_status'] != 201:
                raise AssertionError('Invalid fixture was accepted: '+case['path'])
            if image.mode != 'RGB' or image.info:
                raise AssertionError('Metadata or unsupported mode survived')
    return len(report['cases'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=ROOT/'processed/coffee-v1')
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/demo/coffee-v1')
    args = parser.parse_args()
    result = build(args.prepared, args.output)
    print(json.dumps({'cases': len(result['cases']), 'missing_classes': result['missing_classes'],
                      'output': str(args.output)}, indent=2))
