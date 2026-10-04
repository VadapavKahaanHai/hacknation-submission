"""Audit real mapping inputs; optionally add missing source-folder mappings only."""
import argparse
import hashlib
import json
from pathlib import Path

import coffee_db
import preprocess_coffee as prep
from class_mapping import class_display, validate_classes, MESSAGES, MODEL_CLASS_IDS

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,default=ROOT/'processed/coffee-v1')
    parser.add_argument('--database',type=Path,default=ROOT/'artifacts/databases/coffee.sqlite')
    parser.add_argument('--runtime',type=Path,default=ROOT/'artifacts/runtime/coffee.sqlite')
    parser.add_argument('--report',type=Path,default=ROOT/'artifacts/reports/mapping_audit.json')
    parser.add_argument('--fix-missing',action='store_true')
    args=parser.parse_args()
    protected=[args.prepared/'grouping_review.csv',args.prepared/'splits/images_manifest.csv',args.runtime]
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    db=coffee_db.connect(args.database,readonly=not args.fix_missing)
    try:
        metadata=coffee_db.get_metadata(db)
        inventory_path=args.prepared/'inventory.csv'
        if metadata['inventory_sha256']!=prep.sha(inventory_path.read_bytes()):
            raise ValueError('Inventory differs from catalog build')
        inventory=prep.read_csv(inventory_path)
        old_images=[tuple(r) for r in db.execute('SELECT * FROM images ORDER BY image_id')]
        old_advice=[tuple(r) for r in db.execute('SELECT * FROM advice ORDER BY advice_id')]
        old_predictions=[tuple(r) for r in db.execute('SELECT * FROM predictions ORDER BY prediction_id')]
        if args.fix_missing:
            with db:
                db.execute('BEGIN IMMEDIATE')
                coffee_db.sync_label_maps(db,inventory)
        classes=db.execute('SELECT class_id,label_en,label_hi,type FROM classes ORDER BY class_id').fetchall()
        validate_classes(classes)
        rows,_=coffee_db.manifest_inputs(args.prepared,args.prepared/'splits/images_manifest.csv')
        checks=coffee_db.audit(db,rows)
        maps=[dict(r) for r in db.execute('SELECT * FROM label_map ORDER BY dataset_id,local_folder_name')]
        expected={(r['dataset_id'],r['source_class_name'],int(r['class_id'])) for r in inventory if r['status']=='clean'}
        actual={(r['dataset_id'],r['local_folder_name'],r['class_id']) for r in maps}
        if not expected.issubset(actual):
            raise ValueError('Source-folder mappings are missing; run with --fix-missing')
        for table,key,original in [('images','image_id',old_images),('advice','advice_id',old_advice),
                                   ('predictions','prediction_id',old_predictions)]:
            if original!=[tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY {key}')]:
                raise ValueError('Audit changed protected rows')
    finally:
        db.close()
    runtime=coffee_db.connect(args.runtime,readonly=True)
    try:
        validate_classes(runtime.execute('SELECT class_id,label_en,type FROM classes').fetchall())
    finally:
        runtime.close()
    after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    if after!=before:
        raise ValueError('Protected review, manifest or runtime database changed')
    report={'checks':checks,'classes':[class_display(r) for r in classes],'source_mappings':maps,
            'model_output_class_ids':MODEL_CLASS_IDS,'model_validation':'contract tested; no trained model installed',
            'fallback_codes':list(MESSAGES),'fallback_languages':['en','hi'],
            'source_folder_mapping_count':len(maps),'protected_files_unchanged':True,
            'protected_sha256':after,'migration':'No schema migration; added missing label_map entries only.'}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Mapping audit passed: {len(maps)} source folders, 6 classes, {len(MESSAGES)} bilingual UI messages.')
    print(f'Report: {args.report}')


if __name__=='__main__':
    main()
