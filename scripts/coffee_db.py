"""Build/query the real catalog from a frozen, verified preprocessing manifest."""
import argparse
import json
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import preprocess_coffee as prep
from class_mapping import validate_classes

PROJECT = Path(__file__).resolve().parents[1]
SCHEMA = PROJECT / 'sql' / 'coffee_schema.sql'
CATALOG = PROJECT / 'knowledge' / 'catalog.json'
ADVICE = PROJECT / 'knowledge' / 'advice.csv'
IMAGE_FIELDS = ['image_id','file_path','sha256','phash','width','height','dataset_id',
                'class_id','source_group_id','split','is_augmented','is_field_photo',
                'is_synthetic','is_own_photo']
NUMERIC = {'width','height','class_id','is_augmented','is_field_photo','is_synthetic','is_own_photo'}
ADVICE_FIELDS = ['advice_id','class_id','language','advice_text','action_steps','source_citation',
                 'reviewed_by_human','reviewer','review_date','symptoms','treatment','prevention',
                 'when_to_escalate']
ADVICE_CONTENT = ['class_id','language','advice_text','action_steps','source_citation',
                  'symptoms','treatment','prevention','when_to_escalate']


def connect(path, readonly=False):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) if readonly else sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def get_metadata(db):
    return dict(db.execute('SELECT key,value FROM database_metadata'))


def load_advice(path):
    rows = prep.read_csv(path)
    seen_ids, seen_languages = set(), set()
    for row in rows:
        if set(row) != set(ADVICE_FIELDS):
            raise ValueError('Advice CSV must have exactly the documented columns')
        row['class_id'] = int(row['class_id'])
        row['reviewed_by_human'] = int(row['reviewed_by_human'])
        if row['class_id'] not in prep.CLASSES.values() or row['language'] not in ('en','hi'):
            raise ValueError('Unknown advice class or language')
        if row['reviewed_by_human'] not in (0,1):
            raise ValueError('Review flag must be 0 or 1')
        pair = row['class_id'], row['language']
        if row['advice_id'] in seen_ids or pair in seen_languages:
            raise ValueError('Duplicate advice ID or class/language')
        seen_ids.add(row['advice_id'])
        seen_languages.add(pair)
        for field in ['advice_id','advice_text','action_steps','source_citation']:
            if not row[field].strip():
                raise ValueError(f'Empty required advice field: {field}')
        row['reviewer'] = row['reviewer'].strip() or None
        row['review_date'] = row['review_date'].strip() or None
        if row['reviewed_by_human']:
            if not row['reviewer'] or not row['review_date']:
                raise ValueError('Reviewed advice needs reviewer and UTC review date')
            date = datetime.fromisoformat(row['review_date'].replace('Z','+00:00'))
            if date.tzinfo is None or date.utcoffset().total_seconds() != 0:
                raise ValueError('Review date must use UTC with timezone')
            if date > datetime.now(date.tzinfo):
                raise ValueError('Review date cannot be in the future')
            if any(not row[f].strip() for f in ('symptoms','treatment','prevention','when_to_escalate')):
                raise ValueError('Reviewed advice requires complete structured content')
        elif row['reviewer'] or row['review_date']:
            raise ValueError('Unreviewed advice must not claim a reviewer or review date')
    return rows


def import_advice(db, rows):
    """Idempotent inserts; changed content is always demoted to draft on this import."""
    for row in rows:
        old = db.execute('SELECT * FROM advice WHERE advice_id=?', (row['advice_id'],)).fetchone()
        if old is None:
            db.execute(f"INSERT INTO advice ({','.join(ADVICE_FIELDS)}) VALUES ({','.join('?' for _ in ADVICE_FIELDS)})",
                       [row[f] for f in ADVICE_FIELDS])
            continue
        changed = any(old[f] != row[f] for f in ADVICE_CONTENT)
        if changed:
            if row['reviewed_by_human']:
                raise ValueError('Edited advice must first be imported as an unreviewed draft')
            # Content edits invalidate old review, even if a CSV accidentally retains it.
            db.execute('UPDATE advice SET ' + ','.join(f'{f}=?' for f in ADVICE_CONTENT) +
                       ',reviewed_by_human=0,reviewer=NULL,review_date=NULL WHERE advice_id=?',
                       [row[f] for f in ADVICE_CONTENT] + [row['advice_id']])
        elif any(old[f] != row[f] for f in ('reviewed_by_human','reviewer','review_date')):
            db.execute('UPDATE advice SET reviewed_by_human=?,reviewer=?,review_date=? WHERE advice_id=?',
                       [row['reviewed_by_human'],row['reviewer'],row['review_date'],row['advice_id']])


def manifest_inputs(prepared, manifest):
    prepared, manifest = prepared.resolve(), manifest.resolve()
    if not manifest.is_relative_to(prepared):
        raise ValueError('Manifest must belong to the specified preparation directory')
    summary = json.loads((manifest.parent/'split_summary.json').read_text(encoding='utf-8'))
    checks = {'inventory_sha256': prepared/'inventory.csv',
              'review_sha256': prepared/'grouping_review.csv', 'manifest_sha256': manifest}
    for name, path in checks.items():
        if summary.get(name) != prep.sha(path.read_bytes()):
            raise ValueError(f'{name} changed or is missing; generate a new split version')
    rows = prep.read_csv(manifest)
    if not rows:
        raise ValueError('Refusing to build an empty image catalog')
    inventory = {r['source_path']: r for r in prep.read_csv(prepared/'inventory.csv') if r['status']=='clean'}
    for row in rows:
        source = inventory.get(row['source_path'])
        if source is None or any(row[f] != source[f] for f in prep.MANIFEST_FIELDS
                                 if f not in ('source_group_id','split')):
            raise ValueError('Manifest row does not match its source inventory')
        dataset, label = row['dataset_id'], row['label_en']
        if int(row['class_id']) != prep.CLASSES.get(label):
            raise ValueError('Class ID and label disagree')
        if dataset == 'jmuben' and label not in ('Rust','Cercospora','Phoma'):
            raise ValueError('Wrong JMuBEN class provenance')
        if dataset == 'jmuben2' and label not in ('Healthy','Miner'):
            raise ValueError('Wrong JMuBEN2 class provenance')
        expected = {'jmuben':'train','jmuben2':'train','own_photos':'test','plantdoc':'ood_test'}
        if row['role'] != expected.get(dataset):
            raise ValueError('Unexpected dataset role')
        if dataset == 'plantdoc' and label != 'unknown':
            raise ValueError('PlantDoc is the unknown/non-coffee OOD test')
        if dataset == 'own_photos' and row['is_own_photo'] != '1':
            raise ValueError('Own photo flag is missing')
    if len(rows) != summary['retained_files']:
        raise ValueError('Manifest count and split summary disagree')
    return rows, summary


def image_values(row):
    values = []
    for field in IMAGE_FIELDS:
        value = row[field]
        if field == 'is_augmented' and value == '':
            value = None
        elif field in NUMERIC:
            value = int(value)
        values.append(value)
    return values


def sync_label_maps(db,inventory):
    """Keep provenance mappings even when all images of a class await review."""
    mappings=set()
    for row in inventory:
        if row['status']!='clean':
            continue
        kind,relative=row['source_path'].split('/',1)
        dataset,label,folder,role=prep.classify(kind,Path(relative))
        if (dataset,label,folder,role)!=(row['dataset_id'],row['label_en'],row['source_class_name'],row['role']):
            raise ValueError('Inventory source path and class provenance disagree')
        if int(row['class_id'])!=prep.CLASSES[label]:
            raise ValueError('Inventory class ID mismatch')
        mappings.add((dataset,folder,folder,prep.CLASSES[label]))
    for record in sorted(mappings):
        existing=db.execute('SELECT dataset_id,local_folder_name,source_class_name,class_id FROM label_map '
                            'WHERE dataset_id=? AND local_folder_name=?',record[:2]).fetchone()
        if existing is None:
            db.execute('INSERT INTO label_map VALUES (?,?,?,?)',record)
        elif tuple(existing)!=record:
            raise ValueError('Existing folder mapping conflicts with source inventory')
    return len(mappings)


def audit(db, rows=None):
    validate_classes(db.execute('SELECT class_id,label_en,type FROM classes').fetchall())
    if db.execute('PRAGMA user_version').fetchone()[0] != 2:
        raise ValueError('Unsupported schema version; build a new version-2 database')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Database integrity check failed')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Foreign key violations')
    if db.execute('SELECT * FROM split_leakage').fetchall():
        raise ValueError('Split leakage detected')
    if db.execute('''SELECT c.class_id FROM classes c WHERE c.image_count <>
                     (SELECT count(*) FROM images i WHERE i.class_id=c.class_id)''').fetchall():
        raise ValueError('Cached class image counts disagree')
    if db.execute('''SELECT i.image_id FROM images i JOIN datasets d USING(dataset_id)
                    WHERE d.role='voice' OR (d.role IN ('test','ood_test') AND i.split<>'test')''').fetchall():
        raise ValueError('Invalid dataset split')
    count = db.execute('SELECT count(*) FROM images').fetchone()[0]
    if rows is not None:
        if count != len(rows):
            raise ValueError('Database and manifest image counts differ')
        actual = {r['image_id']: list(r) for r in db.execute('SELECT '+','.join(IMAGE_FIELDS)+' FROM images')}
        if any(actual.get(r['image_id']) != image_values(r) for r in rows):
            raise ValueError('Database image content conflicts with manifest')
    return {'images': count, 'source_groups': db.execute('SELECT count(DISTINCT source_group_id) FROM images').fetchone()[0],
            'training_classes_without_train_images': [r[0] for r in db.execute('''
                SELECT c.label_en FROM classes c WHERE c.type<>'unknown' AND NOT EXISTS
                (SELECT 1 FROM images i WHERE i.class_id=c.class_id AND i.split='train')
                ORDER BY c.class_id''')],
            'advice_records': db.execute('SELECT count(*) FROM advice').fetchone()[0],
            'reviewed_advice': db.execute('SELECT count(*) FROM reviewed_advice').fetchone()[0],
            'pending_sync': db.execute('SELECT count(*) FROM sync_queue').fetchone()[0],
            'checks': 'passed'}


def build_database(prepared, manifest, destination, advice_path=ADVICE, catalog_path=CATALOG):
    prepared, manifest, destination = prepared.resolve(), manifest.resolve(), destination.resolve()
    if destination.is_relative_to(PROJECT/'Data') or destination.is_relative_to(prepared):
        raise ValueError('Keep databases outside raw and processed source directories')
    rows, split_summary = manifest_inputs(prepared, manifest)
    # Always reverify actual files at the import boundary, even after a previous split verification.
    prep.verify(prepared, manifest)
    advice = load_advice(advice_path)
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    metadata = {
        'schema_sha256': prep.sha(SCHEMA.read_bytes()), 'manifest_sha256': prep.sha(manifest.read_bytes()),
        'inventory_sha256': split_summary['inventory_sha256'], 'review_sha256': split_summary['review_sha256'],
        'catalog_sha256': prep.sha(catalog_path.read_bytes()),
        'media_root': Path(os.path.relpath(prepared, destination.parent)).as_posix(),
        'manifest_path': Path(os.path.relpath(manifest, destination.parent)).as_posix(),
        'purpose': 'development_catalog',
        'excluded_source_records': str(split_summary['excluded_source_records']),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    fresh = not destination.exists()
    if fresh:
        handle, name = tempfile.mkstemp(prefix=destination.name+'.', suffix='.tmp', dir=destination.parent)
        os.close(handle)
        working = Path(name)
    else:
        working = destination
    db = None
    try:
        db = connect(working)
        if fresh:
            db.executescript(SCHEMA.read_text(encoding='utf-8'))
        else:
            if db.execute('PRAGMA user_version').fetchone()[0] != 2:
                raise ValueError('Existing database has another schema; use a new destination')
            current = get_metadata(db)
            if any(current.get(k) != v for k,v in metadata.items()):
                raise ValueError('Existing database has different build inputs; use a new destination')
            audit(db, rows)
        with db:
            db.execute('BEGIN IMMEDIATE')
            if fresh:
                for record in catalog['datasets']:
                    columns = list(record)
                    db.execute('INSERT INTO datasets ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',
                               [record[c] for c in columns])
                db.executemany('INSERT INTO classes(class_id,label_en,label_hi,type) VALUES (?,?,?,?)',
                               [(r['class_id'],r['label_en'],r['label_hi'],r['type']) for r in catalog['classes']])
                db.executemany('INSERT INTO images ('+','.join(IMAGE_FIELDS)+') VALUES ('+','.join('?' for _ in IMAGE_FIELDS)+')',
                               (image_values(r) for r in rows))
                sizes = Counter()
                for r in rows:
                    sizes[r['dataset_id']] += prep.checked_media(prepared,r['file_path']).stat().st_size
                db.executemany('UPDATE datasets SET size_mb=? WHERE dataset_id=?',
                               [(n/1_000_000,dataset) for dataset,n in sizes.items()])
                db.executemany('INSERT INTO database_metadata VALUES (?,?)', metadata.items())
            sync_label_maps(db,prep.read_csv(prepared/'inventory.csv'))
            import_advice(db, advice)
            db.execute("INSERT INTO database_metadata VALUES ('advice_csv_sha256',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (prep.sha(advice_path.read_bytes()),))
            report = audit(db, rows)
        db.close()
        db = None
        if fresh:
            if destination.exists():
                raise ValueError('Destination appeared during build; refusing to overwrite it')
            working.rename(destination)
        report.update(database=str(destination), manifest_sha256=metadata['manifest_sha256'],
                      excluded_source_records=split_summary['excluded_source_records'],
                      excluded_groups_by_reason=split_summary['excluded_groups_by_reason'])
        return report
    finally:
        if db is not None:
            db.close()
        if fresh and working.exists():
            working.unlink()  # Only our uniquely named temporary build, never an existing database.


def update_advice(database, path):
    rows = load_advice(path)
    db = connect(database, readonly=False) if database.is_file() else None
    if db is None:
        raise ValueError('Database does not exist')
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            import_advice(db, rows)
            db.execute("INSERT INTO database_metadata VALUES ('advice_csv_sha256',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (prep.sha(path.read_bytes()),))
            return audit(db)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('build')
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--advice', type=Path, default=ADVICE)
    p = commands.add_parser('import-advice')
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--advice', type=Path, default=ADVICE)
    for command in ('check','counts','advice'):
        p = commands.add_parser(command)
        p.add_argument('--database', type=Path, required=True)
        if command == 'advice':
            p.add_argument('--class-id', type=int, required=True)
            p.add_argument('--language', choices=['en','hi'], default='en')
    args = parser.parse_args()
    try:
        if args.command == 'build':
            result = build_database(args.prepared,args.manifest,args.database,args.advice)
        elif args.command == 'import-advice':
            result = update_advice(args.database,args.advice)
        else:
            db = connect(args.database, readonly=True)
            try:
                if args.command == 'check':
                    meta = get_metadata(db)
                    rows,_ = manifest_inputs(args.database.parent/Path(meta['media_root']),
                                             args.database.parent/Path(meta['manifest_path']))
                    prep.verify((args.database.parent/Path(meta['media_root'])).resolve(),
                                (args.database.parent/Path(meta['manifest_path'])).resolve())
                    result = audit(db,rows)
                elif args.command == 'counts':
                    result = [dict(r) for r in db.execute('SELECT * FROM class_image_counts WHERE image_count>0')]
                else:
                    result = [dict(r) for r in db.execute('SELECT * FROM reviewed_advice WHERE class_id=? AND language=?',
                                                          (args.class_id,args.language))]
            finally:
                db.close()
        print(json.dumps(result,indent=2,ensure_ascii=False))
    except (OSError,ValueError,KeyError,sqlite3.Error) as error:
        parser.exit(1,f'Error: {error}\n')


if __name__ == '__main__':
    main()
