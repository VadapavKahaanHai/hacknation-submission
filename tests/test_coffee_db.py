"""Integration tests use temporary synthetic images and disposable SQLite databases."""
import argparse
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import coffee_db as app
import pipeline
import preprocess_coffee as prep


class DatabaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='coffee-db-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root/'raw'
        self.prepared = self.root/'prepared'
        self.database = self.root/'databases'/'coffee.sqlite'
        self.advice = self.root/'advice.csv'
        self.advice.write_bytes(app.ADVICE.read_bytes())
        rng = np.random.default_rng(321)
        for label in ('Leaf rust','Cerscospora','Phoma','Miner','Healthy'):
            folder = self.raw/label
            folder.mkdir(parents=True)
            Image.fromarray(rng.integers(0,255,(48,64,3),dtype=np.uint8)).save(folder/'a.png')
        with contextlib.redirect_stdout(io.StringIO()):
            prep.prepare(argparse.Namespace(jmuben=self.raw,plantdoc=None,own_photos=None,
                         own_groups=None,output=self.prepared,radius=0))
            prep.split(argparse.Namespace(output=self.prepared,name='splits',seed=42,same_leaf=None))
        self.manifest = self.prepared/'splits'/'images_manifest.csv'

    def build(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return app.build_database(self.prepared,self.manifest,self.database,self.advice,**kwargs)

    def query(self, query):
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            return db.execute(query).fetchall()

    def test_build_repeat_and_pipeline(self):
        report = self.build()
        self.assertEqual(report['images'],5)
        self.assertEqual(report['advice_records'],6)
        self.assertEqual(report['reviewed_advice'],0)
        self.assertEqual(report['training_classes_without_train_images'],[])
        self.assertEqual(self.query('SELECT count(*) FROM model_versions'),[(0,)])
        self.assertEqual(self.query('SELECT count(*) FROM predictions'),[(0,)])
        self.assertEqual(self.query('SELECT count(*) FROM images WHERE is_augmented IS NULL'),[(5,)])
        self.assertEqual(self.query('SELECT sum(image_count) FROM classes'),[(5,)])
        self.assertEqual(self.build()['images'],5)
        args = argparse.Namespace(prepared=self.prepared,jmuben=self.raw,plantdoc=None,
              own_photos=None,own_groups=None,radius=0,split_name='splits',seed=42,
              same_leaf=None,database=self.database,advice=self.advice,reports=self.root/'reports',
              require_complete_review=True)
        with contextlib.redirect_stdout(io.StringIO()):
            result = pipeline.run(args)
        self.assertEqual(result['checks'],'passed')
        self.assertTrue((self.root/'reports'/'DATA_REPORT.md').is_file())
        # A lost preparation or changed frozen manifest cannot silently populate a DB.
        self.manifest.write_text(self.manifest.read_text(encoding='utf-8')+'\n',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'manifest_sha256'):
            self.build()
        self.assertEqual(self.query('SELECT count(*) FROM images'),[(5,)])

    def test_failed_fresh_build_leaves_no_database(self):
        catalog = json.loads(app.CATALOG.read_text(encoding='utf-8'))
        catalog['classes'] = [c for c in catalog['classes'] if c['class_id']!=5]
        broken = self.root/'broken_catalog.json'
        broken.write_text(json.dumps(catalog),encoding='utf-8')
        with self.assertRaises(sqlite3.IntegrityError):
            self.build(catalog_path=broken)
        self.assertFalse(self.database.exists())
        self.assertEqual(list(self.database.parent.glob('*.tmp')),[])

    def test_pipeline_from_raw_inputs(self):
        args = argparse.Namespace(prepared=self.root/'fresh-preparation',jmuben=self.raw,plantdoc=None,
              own_photos=None,own_groups=None,radius=0,split_name='splits',seed=42,
              same_leaf=None,database=self.database,advice=self.advice,reports=self.root/'reports',
              require_complete_review=True)
        with contextlib.redirect_stdout(io.StringIO()):
            result = pipeline.run(args)
        self.assertEqual(result['images'],5)
        self.assertTrue((args.prepared/'prepare_summary.json').is_file())
        self.assertTrue((args.reports/'build_report.json').is_file())

    def test_advice_review_edit_and_transaction_rollback(self):
        self.build()
        rows = prep.read_csv(self.advice)
        row = rows[0]
        row.update(reviewed_by_human='1',reviewer='TEST-ONLY',review_date='2020-01-01T00:00:00Z')
        prep.write_csv(self.advice,app.ADVICE_FIELDS,rows)
        self.assertEqual(app.update_advice(self.database,self.advice)['reviewed_advice'],1)
        self.assertEqual(app.update_advice(self.database,self.advice)['reviewed_advice'],1)
        # A stale approval cannot approve an edited record.
        row['treatment'] = 'Changed test draft'
        prep.write_csv(self.advice,app.ADVICE_FIELDS,rows)
        with self.assertRaisesRegex(ValueError,'unreviewed draft'):
            app.update_advice(self.database,self.advice)
        self.assertEqual(self.query('SELECT count(*) FROM reviewed_advice'),[(1,)])
        row.update(reviewed_by_human='0',reviewer='',review_date='')
        prep.write_csv(self.advice,app.ADVICE_FIELDS,rows)
        self.assertEqual(app.update_advice(self.database,self.advice)['reviewed_advice'],0)
        old = self.query('SELECT treatment FROM advice WHERE advice_id="rust-en"')
        # First row updates successfully, then a later uniqueness failure must roll back both.
        row['treatment'] = 'Must roll back'
        rows[1]['advice_id'] = 'duplicate-language'
        prep.write_csv(self.advice,app.ADVICE_FIELDS,rows)
        with self.assertRaises(sqlite3.IntegrityError):
            app.update_advice(self.database,self.advice)
        self.assertEqual(self.query('SELECT treatment FROM advice WHERE advice_id="rust-en"'),old)
        self.assertEqual(self.query('SELECT count(*) FROM advice'),[(6,)])

    def test_existing_image_conflict_and_media_tamper(self):
        self.build()
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute('UPDATE images SET width=width+1')
            db.commit()
        with self.assertRaisesRegex(ValueError,'conflicts with manifest'):
            self.build()
        row = prep.read_csv(self.manifest)[0]
        (self.prepared/row['file_path']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            self.build()

    def test_unreviewed_and_malformed_inputs_rejected(self):
        rows = prep.read_csv(self.advice)
        rows[0]['reviewed_by_human'] = '1'
        prep.write_csv(self.advice,app.ADVICE_FIELDS,rows)
        with self.assertRaisesRegex(ValueError,'reviewer'):
            self.build()
        self.assertFalse(self.database.exists())


if __name__ == '__main__':
    unittest.main()
