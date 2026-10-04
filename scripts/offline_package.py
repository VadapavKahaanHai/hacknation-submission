"""Build, validate and install a small offline content seed; never overwrite device data."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from class_mapping import CLASS_IDS, DISPLAY_NAMES, MESSAGES, validate_classes
from runtime_db import ROOT, initialize_runtime

FILES = {'coffee.sqlite', 'ui_messages.json', 'class_labels.json'}
MAX_PACKAGE_BYTES = 16 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8')


def inspect_db(path, seed=False):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        if db.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok':
            raise ValueError('SQLite integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('SQLite foreign key check failed')
        if db.execute('PRAGMA user_version').fetchone()[0] != 2:
            raise ValueError('Unsupported database schema')
        validate_classes(db.execute('SELECT class_id,label_en,type FROM classes').fetchall())
        metadata = dict(db.execute('SELECT key,value FROM database_metadata'))
        if metadata.get('purpose') != ('runtime' if seed else 'development_catalog'):
            raise ValueError('Wrong database purpose')
        counts = {table: db.execute('SELECT count(*) FROM '+table).fetchone()[0]
                  for table in ('classes', 'advice', 'images', 'predictions', 'escalations',
                                'sync_queue', 'datasets', 'label_map', 'voice_phrases', 'model_versions')}
        if seed:
            if any(counts[t] for t in ('images', 'predictions', 'escalations', 'sync_queue',
                                       'datasets', 'label_map', 'voice_phrases', 'model_versions')):
                raise ValueError('Seed contains operational, training or model data')
            if db.execute('SELECT count(*) FROM prediction_media').fetchone()[0]:
                raise ValueError('Seed contains operational media')
            if db.execute('SELECT count(*) FROM advice WHERE reviewed_by_human != 1').fetchone()[0]:
                raise ValueError('Seed contains unreviewed advice')
            if metadata.get('inference_mode') != 'unconfigured' or metadata.get('media_root') != 'media':
                raise ValueError('Unexpected runtime configuration')
            if db.execute('SELECT count(*) FROM classes WHERE image_count != 0').fetchone()[0]:
                raise ValueError('Seed contains stale image counts')
        elif db.execute('SELECT count(*) FROM split_leakage').fetchone()[0]:
            raise ValueError('Catalog has split leakage')
        return {'counts': counts, 'schema_version': 2}


def validate(package):
    package = Path(package)
    if package.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError('Package exceeds 16 MiB limit')
    with zipfile.ZipFile(package) as archive:
        entries = archive.infolist()
        if len(entries) != 4 or {i.filename for i in entries} != FILES | {'manifest.json'}:
            raise ValueError('Unexpected, duplicate or unsafe archive paths')
        if sum(i.file_size for i in entries) > MAX_PACKAGE_BYTES:
            raise ValueError('Expanded package exceeds 16 MiB limit')
        # Read only fixed members. Never extract archive-controlled paths.
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('package_version') != 1 or manifest.get('schema_version') != 2:
            raise ValueError('Unsupported package version')
        if set(manifest.get('files', {})) != FILES:
            raise ValueError('Invalid file manifest')
        contents = {name: archive.read(name) for name in FILES}
    for name, data in contents.items():
        if manifest['files'][name] != {'sha256': digest(data), 'bytes': len(data)}:
            raise ValueError('File hash/size mismatch: '+name)
    labels = json.loads(contents['class_labels.json'])
    expected_labels = [{'class_id': i, 'label_en': label, 'display_name_en': DISPLAY_NAMES[i][0],
                        'display_name_hi': DISPLAY_NAMES[i][1]} for label, i in CLASS_IDS.items()]
    if labels != expected_labels or json.loads(contents['ui_messages.json']) != MESSAGES:
        raise ValueError('UI content does not match this application version')
    with tempfile.TemporaryDirectory(prefix='coffee-package-check-') as temporary:
        db_path = Path(temporary)/'coffee.sqlite'
        db_path.write_bytes(contents['coffee.sqlite'])
        report = inspect_db(db_path, seed=True)
    if manifest.get('counts') != report['counts']:
        raise ValueError('Incorrect packaged row counts')
    expected_availability = {'inference': 'unconfigured', 'approved_advice_records': report['counts']['advice'],
                             'ui_languages': ['en', 'hi'], 'ui_human_reviewed': False}
    if manifest.get('availability') != expected_availability:
        raise ValueError('Incorrect availability claims')
    return manifest


def build(catalog, output):
    catalog, output = Path(catalog).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Package exists; choose a new version/path')
    # SQLite backup captures a consistent snapshot, including committed WAL pages.
    with tempfile.TemporaryDirectory(prefix='coffee-package-build-') as temporary:
        work = Path(temporary)
        snapshot = work/'catalog.sqlite'
        with closing(sqlite3.connect(catalog.as_uri()+'?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
        inspect_db(snapshot)
        database = work/'coffee.sqlite'
        initialize_runtime(snapshot, database, mock=False)
        report = inspect_db(database, seed=True)
        contents = {'coffee.sqlite': database.read_bytes(), 'ui_messages.json': json_bytes(MESSAGES),
                    'class_labels.json': json_bytes([
                        {'class_id': i, 'label_en': label, 'display_name_en': DISPLAY_NAMES[i][0],
                         'display_name_hi': DISPLAY_NAMES[i][1]} for label, i in CLASS_IDS.items()])}
        manifest = dict(package_version=1, schema_version=2, counts=report['counts'],
                        source_snapshot_sha256=digest(snapshot.read_bytes()),
                        availability={'inference': 'unconfigured', 'approved_advice_records': report['counts']['advice'],
                                      'ui_languages': ['en', 'hi'], 'ui_human_reviewed': False},
                        files={name: {'sha256': digest(data), 'bytes': len(data)} for name, data in contents.items()})
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=output.name+'.', suffix='.tmp', dir=output.parent)
        os.close(fd)
        staged = Path(name)
        try:
            with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for filename, data in contents.items():
                    archive.writestr(filename, data)
                archive.writestr('manifest.json', json_bytes(manifest))
            validate(staged)
            # Same-volume hard link publishes the validated file without overwrite.
            with staged.open('r+b') as complete:
                os.fsync(complete.fileno())
            os.link(staged, output)
        finally:
            staged.unlink(missing_ok=True)
    return manifest


def install(package, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Destination exists; never replace an active local database')
    # Validate and install the same bytes, even if the input path changes concurrently.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='coffee-install-', dir=destination.parent) as temporary:
        staging = Path(temporary)
        snapshot = staging/'package.zip'
        package = Path(package)
        if package.stat().st_size > MAX_PACKAGE_BYTES:
            raise ValueError('Package exceeds 16 MiB limit')
        snapshot.write_bytes(package.read_bytes())
        manifest = validate(snapshot)
        seed = staging/'seed'
        seed.mkdir()
        with zipfile.ZipFile(snapshot) as archive:
            for name in FILES | {'manifest.json'}:
                (seed/name).write_bytes(archive.read(name))
        (seed/'media').mkdir()
        if destination.exists():
            raise ValueError('Destination appeared during installation')
        seed.rename(destination)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    builder = commands.add_parser('build')
    builder.add_argument('--catalog', type=Path, default=ROOT/'artifacts/databases/coffee.sqlite')
    builder.add_argument('--output', type=Path, default=ROOT/'artifacts/packages/coffee-local-v1.zip')
    checker = commands.add_parser('validate')
    checker.add_argument('package', type=Path)
    installer = commands.add_parser('install')
    installer.add_argument('package', type=Path)
    installer.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'build':
        result = build(args.catalog, args.output)
    elif args.command == 'validate':
        result = validate(args.package)
    else:
        result = install(args.package, args.destination)
    print(json.dumps({'checks': 'passed', 'counts': result['counts'],
                      'availability': result['availability']}, indent=2))
