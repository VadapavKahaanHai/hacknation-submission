"""Demo pack generation and API checks use temporary images/databases only."""
import base64
import csv
import io
import json
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import prepare_demo as demo
import test_runtime_api as fixture
from runtime_db import clean_upload


class DemoPackTests(unittest.TestCase):
    setUp = fixture.RuntimeTests.setUp
    request = fixture.RuntimeTests.request
    payload = fixture.RuntimeTests.payload
    scalar = fixture.RuntimeTests.scalar

    def prepare(self):
        prepared = self.root/'prepared'
        (prepared/'media').mkdir(parents=True)
        (prepared/'splits').mkdir()
        buffer = io.BytesIO()
        Image.new('RGB', (30, 20), 'green').save(buffer, 'PNG')
        data = buffer.getvalue()
        (prepared/'media/test.png').write_bytes(data)
        row = dict(image_id=demo.sha(data), class_id='1', label_en='Rust', dataset_id='jmuben',
                   doi='10.17632/t2r6rszp5c.1', source_path='jmuben/Leaf rust/test.jpg',
                   file_path='media/test.png', source_group_id='test-group', split='test',
                   sha256=demo.sha(data), width='30', height='20')
        manifest = prepared/'splits/images_manifest.csv'
        with manifest.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
            # A Healthy training row must never be chosen for the demo.
            writer.writerow(dict(row, class_id='5', label_en='Healthy', split='train'))
        summary = {'manifest_sha256': demo.sha(manifest.read_bytes())}
        for name, key in [('inventory.csv', 'inventory_sha256'), ('grouping_review.csv', 'review_sha256')]:
            (prepared/name).write_bytes(b'test fixture\n')
            summary[key] = demo.sha((prepared/name).read_bytes())
        (manifest.parent/'split_summary.json').write_text(json.dumps(summary), encoding='utf-8')
        return prepared

    def test_pack_cases_through_hindi_api(self):
        prepared = self.prepare()
        output = self.root/'demo'
        report = demo.build(prepared, output)
        self.assertEqual(len(report['cases']), 15)
        self.assertIn('Healthy', report['missing_classes'])
        accepted = 0
        for case in report['cases']:
            with self.subTest(case=case['path']):
                body = self.payload()
                body.update(language='hi', image_base64=base64.b64encode((output/case['path']).read_bytes()).decode())
                status, result, headers = self.request('POST', '/predictions', body)
                self.assertEqual(status, case['expected_http_status'])
                if status == 201:
                    accepted += 1
                    self.assertEqual(result['top_class_id'], 6)
                    self.assertEqual(result['advice'], [])
                    self.assertEqual(result['status'], 'not_sure_ask_a_person')
                    self.assertTrue(all(f['language'] == 'hi' for f in result['fallbacks']))
                    self.assertEqual(result['top_class']['display_name'], 'अज्ञात / अनिश्चित')
                else:
                    self.assertEqual(result['error'], case['expected_error'])
                    self.assertEqual(result['fallback']['language'], 'hi')
        self.assertEqual(self.scalar('SELECT count(*) FROM predictions'), accepted)
        self.assertEqual(self.scalar('SELECT count(*) FROM escalations'), accepted)
        self.assertEqual(self.scalar('SELECT count(*) FROM sync_queue'), accepted)

    def test_orientation_alpha_and_utf8(self):
        output = self.root/'demo'
        demo.build(self.prepare(), output)
        raw = (output/'edge_cases/orientation.jpg').read_bytes()
        image, normalized = clean_upload(raw)
        self.assertEqual(image.size, (32, 48))
        with Image.open(io.BytesIO(raw)) as source:
            self.assertEqual(image.tobytes(), ImageOps.exif_transpose(source).convert('RGB').tobytes())
        with Image.open(io.BytesIO(normalized)) as saved:
            self.assertEqual(saved.info, {})
            self.assertFalse(saved.getexif())
        alpha, _ = clean_upload((output/'edge_cases/transparent.png').read_bytes())
        self.assertEqual(alpha.getpixel((0, 0)), (255, 255, 255))
        texts = json.loads((output/'local_language.json').read_text(encoding='utf-8'))
        self.assertFalse(texts['human_reviewed'])
        self.assertEqual(len(texts['classes']), 6)
        self.assertEqual(len(texts['messages']), 11)
        for code, translations in texts['messages'].items():
            self.assertEqual(set(translations), {'en', 'hi'})
            self.assertNotIn('\ufffd', translations['hi'])
            self.assertTrue(any('\u0900' <= c <= '\u097f' for c in translations['hi']))
        response = self.request('GET', '/fallbacks', query='language=hi')[1]
        self.assertEqual({r['code']: r['text'] for r in response},
                         {code: values['hi'] for code, values in texts['messages'].items()})

    def test_refuses_overwrite_and_changed_inputs(self):
        prepared = self.prepare()
        output = self.root/'demo'
        demo.build(prepared, output)
        with self.assertRaises(ValueError):
            demo.build(prepared, output)
        with self.assertRaises(ValueError):
            demo.build(prepared, prepared/'demo')
        (prepared/'media/test.png').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            demo.build(prepared, self.root/'other')
        self.assertFalse((self.root/'other').exists())
        (prepared/'grouping_review.csv').write_bytes(b'changed review')
        with self.assertRaises(ValueError):
            demo.build(prepared, self.root/'other')
        (output/'edge_cases/blank.png').write_bytes(b'changed fixture')
        with self.assertRaises(ValueError):
            demo.verify(output)


if __name__ == '__main__':
    unittest.main()
