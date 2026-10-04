"""Offline seed packaging, validation and first-install safety."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sys
import sqlite3
import unittest
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import offline_package as package
import test_runtime_api as fixture
from runtime_db import Runtime, RequestError


class OfflinePackageTests(unittest.TestCase):
    setUp = fixture.RuntimeTests.setUp

    def build(self):
        archive = self.root/'seed.zip'
        manifest = package.build(self.catalog, archive)
        return archive, manifest

    def rewrite(self, archive, change):
        with zipfile.ZipFile(archive) as source:
            files = {name: source.read(name) for name in source.namelist()}
        change(files)
        target = self.root/'changed.zip'
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as dest:
            for name, data in files.items():
                dest.writestr(name, data)
        return target

    def test_round_trip_and_offline_queue_after_restart(self):
        archive, manifest = self.build()
        self.assertEqual(package.validate(archive), manifest)
        self.assertEqual(manifest['counts']['advice'], 1)
        self.assertEqual(manifest['availability']['inference'], 'unconfigured')
        destination = self.root/'installed'
        package.install(archive, destination)
        runtime = Runtime(destination/'coffee.sqlite')
        self.assertEqual(len(runtime.classes('hi')), 6)
        self.assertEqual(runtime.get_reviewed_advice(2), [])
        self.assertEqual(runtime.get_reviewed_advice(1)[0]['advice_text'], 'TEST APPROVED')
        self.assertEqual(runtime.list_pending_sync(), [])
        with self.assertRaises(RequestError) as error:
            runtime.submit_image(fixture.new_id(), self.device, self.photo)
        self.assertEqual(error.exception.code, 'model_not_configured')
        self.assertEqual(list((destination/'media').iterdir()), [])
        # Test-only adapter, registered only in this temporary installed database.
        with runtime.connection(write=True) as db:
            db.execute("""INSERT INTO model_versions(model_id,version,base_model,quantization,
                       file_size_mb,confidence_threshold,min_margin)
                       VALUES ('test','test','TEST ONLY','int8',1,0.8,0.1)""")
        runtime = Runtime(destination/'coffee.sqlite', predictor=lambda im: [0.1]*5+[0.5], model_id='test')
        result, _ = runtime.submit_image(fixture.new_id(), self.device, self.photo, 'hi')
        restarted = Runtime(destination/'coffee.sqlite')
        queued = restarted.list_pending_sync()
        self.assertEqual(len(queued), 1)
        self.assertEqual(restarted.get_prediction(result['prediction_id'], 'hi')['status'], 'not_sure_ask_a_person')
        with Image.open(destination/result['image_path']) as image:
            self.assertFalse(image.getexif())
        self.assertEqual(hashlib.sha256(self.catalog.read_bytes()).hexdigest(), self.catalog_hash)
        before = (destination/'coffee.sqlite').read_bytes()
        with self.assertRaises(ValueError):
            package.install(archive, destination)
        self.assertEqual((destination/'coffee.sqlite').read_bytes(), before)
        self.assertEqual(len(restarted.list_pending_sync()), 1)

    def test_tamper_and_path_injection_rejected_without_install(self):
        archive, _ = self.build()
        bad = self.rewrite(archive, lambda files: files.update({'ui_messages.json': b'{}'}))
        with self.assertRaises(ValueError):
            package.install(bad, self.root/'rejected')
        self.assertFalse((self.root/'rejected').exists())
        bad = self.rewrite(archive, lambda files: files.update({'../escape.txt': b'bad'}))
        with self.assertRaises(ValueError):
            package.validate(bad)
        self.assertFalse((self.root/'escape.txt').exists())

    def test_semantic_checks_even_with_recomputed_hashes(self):
        archive, _ = self.build()
        def change(files):
            # A hash alone must not make malformed/mismatched local text valid.
            files['class_labels.json'] = b'[]'
            manifest = json.loads(files['manifest.json'])
            manifest['files']['class_labels.json'] = {'bytes': 2, 'sha256': package.digest(b'[]')}
            files['manifest.json'] = package.json_bytes(manifest)
        with self.assertRaises(ValueError):
            package.validate(self.rewrite(archive, change))
        def false_claim(files):
            manifest = json.loads(files['manifest.json'])
            manifest['availability']['inference'] = 'ready'
            files['manifest.json'] = package.json_bytes(manifest)
        with self.assertRaises(ValueError):
            package.validate(self.rewrite(archive, false_claim))

    def test_runtime_source_and_overwrite_rejected(self):
        archive, _ = self.build()
        before = archive.read_bytes()
        with self.assertRaises(ValueError):
            package.build(self.catalog, archive)
        self.assertEqual(archive.read_bytes(), before)
        with self.assertRaises(ValueError):
            package.build(self.database, self.root/'private.zip')
        self.assertFalse((self.root/'private.zip').exists())

    def test_unreviewed_advice_rejected_even_with_valid_checksum(self):
        archive, _ = self.build()
        def change(files):
            altered = self.root/'altered.sqlite'
            altered.write_bytes(files['coffee.sqlite'])
            with closing(sqlite3.connect(altered)) as db:
                db.execute("UPDATE advice SET reviewed_by_human=0, reviewer=NULL, review_date=NULL")
                db.commit()
            data = altered.read_bytes()
            files['coffee.sqlite'] = data
            manifest = json.loads(files['manifest.json'])
            manifest['files']['coffee.sqlite'] = {'bytes': len(data), 'sha256': package.digest(data)}
            files['manifest.json'] = package.json_bytes(manifest)
        with self.assertRaisesRegex(ValueError, 'unreviewed'):
            package.validate(self.rewrite(archive, change))


if __name__ == '__main__':
    unittest.main()
