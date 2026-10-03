"""Reuse completed cleaning, create reviewed/exclusion-safe splits, and build SQLite."""
import argparse
import json
import sqlite3
from pathlib import Path

import coffee_db
import preprocess_coffee as prep

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    prepared = args.prepared.resolve()
    if not prepared.exists():
        prep.prepare(argparse.Namespace(jmuben=args.jmuben,plantdoc=args.plantdoc,
                     own_photos=args.own_photos,own_groups=args.own_groups,
                     output=prepared,radius=args.radius))
    elif not (prepared/'prepare_summary.json').is_file():
        raise ValueError('Incomplete preparation directory. Use a new --prepared directory.')
    elif args.own_photos is not None or args.own_groups is not None:
        raise ValueError('To add/relabel own photos use a new --prepared directory; existing data is frozen.')
    status = json.loads((prepared/'prepare_summary.json').read_text(encoding='utf-8'))
    if not status.get('complete'):
        raise ValueError('Preparation is incomplete')
    reviews = prep.read_csv(prepared/'grouping_review.csv')
    pending = sum(r['decision'].strip().lower() in ('','pending') for r in reviews)
    print(f'Preparation: {status["counts"]}; unique files: {status["unique_clean_files"]}; pending pairs: {pending}', flush=True)
    if args.require_complete_review and pending:
        raise ValueError('Near-duplicate review is incomplete. Resolve grouping_review.csv first.')
    split_dir = (prepared/args.split_name).resolve()
    if split_dir.parent != prepared:
        raise ValueError('--split-name must be a direct child of the preparation directory')
    manifest = split_dir/'images_manifest.csv'
    if not split_dir.exists():
        # The importer verifies these same files before any database write.
        prep.split(argparse.Namespace(output=prepared,name=args.split_name,seed=args.seed,
                                      same_leaf=args.same_leaf,verify_files=False))
    else:
        summary = json.loads((split_dir/'split_summary.json').read_text(encoding='utf-8'))
        expected_edges = prep.sha(args.same_leaf.read_bytes()) if args.same_leaf else None
        if summary['seed'] != args.seed or summary.get('same_leaf_sha256') != expected_edges:
            raise ValueError('Split settings changed; use a new --split-name and --database.')
    report = coffee_db.build_database(prepared,manifest,args.database,args.advice)
    report['preparation'] = status
    report['pending_review_pairs'] = pending
    report['catalog_status'] = 'partial_review_exclusions' if report['excluded_source_records'] else 'all_clean_groups_included'
    reports = args.reports.resolve()
    reports.mkdir(parents=True,exist_ok=True)
    prep.write_json(reports/'build_report.json',report)
    db = coffee_db.connect(args.database,readonly=True)
    try:
        counts = [dict(r) for r in db.execute('SELECT * FROM class_image_counts WHERE image_count>0')]
    finally:
        db.close()
    prep.write_csv(reports/'database_counts.csv',
                   ['dataset_id','dataset','class_id','label_en','split','image_count','source_group_count'],counts)
    lines = ['# Data build report','',f'- Database: `{args.database.resolve()}`',
             f'- Catalog status: {report["catalog_status"]}',
             f'- Images: {report["images"]}; source groups: {report["source_groups"]}',
             f'- Excluded source records: {report["excluded_source_records"]}',
             f'- Pending review pairs: {pending}',
             f'- Training classes without training images: {report["training_classes_without_train_images"]}',
             f'- Advice records: {report["advice_records"]}; human-reviewed: {report["reviewed_advice"]}',
             '- Foreign keys, integrity, stored media and manifest/database parity: passed.',
             '- This is a development catalog. It is not a deployable model or a validated treatment library.',
             '', '## Retained images', '', '| Dataset | Class | Split | Files | Groups |',
             '|---|---|---|---:|---:|']
    lines += [f'| {r["dataset"]} | {r["label_en"]} | {r["split"]} | {r["image_count"]} | {r["source_group_count"]} |' for r in counts]
    (reports/'DATA_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,default=ROOT/'processed'/'coffee-v1')
    parser.add_argument('--jmuben',type=Path,default=ROOT/'Data'/'JMuBEN')
    parser.add_argument('--plantdoc',type=Path,default=ROOT/'Data'/'PlantDoc')
    parser.add_argument('--own-photos',type=Path)
    parser.add_argument('--own-groups',type=Path)
    parser.add_argument('--radius',type=int,choices=range(9),default=4)
    parser.add_argument('--split-name',default='splits')
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--same-leaf',type=Path)
    parser.add_argument('--database',type=Path,default=ROOT/'artifacts'/'databases'/'coffee.sqlite')
    parser.add_argument('--advice',type=Path,default=coffee_db.ADVICE)
    parser.add_argument('--reports',type=Path,default=ROOT/'artifacts'/'reports')
    parser.add_argument('--require-complete-review',action='store_true',help='Stop if any near-match pair is pending')
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError,KeyError,OSError,sqlite3.Error) as error:
        parser.exit(1,f'Error: {error}\n')


if __name__ == '__main__':
    main()
