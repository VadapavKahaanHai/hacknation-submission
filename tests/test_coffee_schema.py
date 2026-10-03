"""Run with: python tests/test_coffee_schema.py (Python stdlib only)."""
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[1] / 'sql'
db = sqlite3.connect(':memory:')
db.executescript((root / 'coffee_schema.sql').read_text(encoding='utf-8'))
db.executescript((root / 'coffee_sample_data.sql').read_text(encoding='utf-8'))


def scalar(sql, args=()):
    return db.execute(sql, args).fetchone()[0]


def rejected(sql, args=()):
    db.execute('SAVEPOINT reject_check')
    try:
        db.execute(sql, args)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError(f'Invalid write accepted: {sql}')
    finally:
        db.execute('ROLLBACK TO reject_check')
        db.execute('RELEASE reject_check')


tables = ['datasets', 'classes', 'label_map', 'images', 'advice',
          'voice_phrases', 'model_versions', 'predictions', 'escalations', 'sync_queue']
assert {t: scalar(f'SELECT count(*) FROM {t}') for t in tables} == {
    t: 6 if t == 'classes' else 5 for t in tables}
assert not db.execute('PRAGMA foreign_key_check').fetchall()
assert scalar('PRAGMA integrity_check') == 'ok'
assert scalar('SELECT count(*) FROM split_leakage') == 0
assert scalar('SELECT count(*) FROM reviewed_advice') == 0
assert scalar('SELECT sum(image_count) FROM classes') == 5
assert scalar('SELECT sum(image_count) FROM class_image_counts') == 5
assert scalar("SELECT count(*) FROM predictions WHERE status='not_sure_ask_a_person'") == 5
assert scalar("SELECT reason FROM escalations WHERE prediction_id='pred-demo-3'") == 'close_scores'
assert scalar("SELECT reason FROM escalations WHERE prediction_id='pred-demo-4'") == 'unknown_input'

# Same-group near duplicate may enter the same split, but never another split.
image_insert = '''INSERT INTO images(image_id,file_path,sha256,phash,width,height,
    dataset_id,class_id,source_group_id,split) VALUES (?,?,?,?,224,224,?,?,?,?)'''
rejected(image_insert, ('duplicate', 'dup.jpg', '1'*64, 'a'*16,
                       'jmuben', 1, 'leaf-demo-1', 'train'))
rejected(image_insert, ('leak', 'leak.jpg', 'a'*64, 'a'*16,
                       'jmuben', 1, 'leaf-demo-1', 'test'))
db.execute(image_insert, ('near', 'near.jpg', 'a'*64, 'a'*16,
                         'jmuben', 1, 'leaf-demo-1', 'train'))
rejected("UPDATE images SET split='val' WHERE image_id='near'")
rejected("UPDATE images SET source_group_id='leaf-demo-2' WHERE image_id='near'")
rejected(image_insert, ('ood', 'ood.jpg', 'b'*64, 'b'*16,
                       'plantdoc', 6, 'ood-leaf', 'train'))
rejected(image_insert, ('voice', 'voice.jpg', 'b'*64, 'b'*16,
                       'common_voice_hi', 6, 'voice-leaf', 'test'))
rejected(image_insert, ('own', 'own.jpg', 'b'*64, 'b'*16,
                       'own_photos', 1, 'own-leaf', 'test'))
rejected("UPDATE images SET dataset_id='plantdoc' WHERE image_id='near'")
rejected("UPDATE datasets SET role='test' WHERE dataset_id='jmuben'")
db.execute("UPDATE images SET class_id=2 WHERE image_id='near'")
assert scalar('SELECT image_count FROM classes WHERE class_id=1') == 1
assert scalar('SELECT image_count FROM classes WHERE class_id=2') == 2
db.execute("DELETE FROM images WHERE image_id='near'")
assert scalar('SELECT sum(image_count) FROM classes') == 5

# Caller cannot bypass the human review and model decision state.
rejected("UPDATE advice SET reviewed_by_human=1 WHERE advice_id='advice-demo-1'")
db.execute("""UPDATE advice SET reviewed_by_human=1, reviewer='TEST-ONLY',
    review_date='2026-10-03T00:00:00.000Z' WHERE advice_id='advice-demo-1'""")
assert scalar('SELECT count(*) FROM reviewed_advice') == 1
assert scalar('SELECT count(*) FROM prediction_advice') == 0
db.execute("UPDATE advice SET advice_text='Changed draft' WHERE advice_id='advice-demo-1'")
assert scalar('SELECT count(*) FROM reviewed_advice') == 0
rejected("UPDATE predictions SET status='confident' WHERE prediction_id='pred-demo-1'")
rejected("UPDATE predictions SET confidence=0.9 WHERE prediction_id='pred-demo-1'")
rejected("UPDATE model_versions SET confidence_threshold=0.1 WHERE model_id='model-demo-1'")
rejected("UPDATE classes SET type='healthy' WHERE class_id=6")
rejected("UPDATE predictions SET synced=1 WHERE prediction_id='pred-demo-1'")
rejected("UPDATE escalations SET resolved=1 WHERE prediction_id='pred-demo-1'")
rejected("INSERT INTO sync_queue(row_id) VALUES ('missing-prediction')")

# At the threshold is confident; a tie is uncertain; foreign keys are enforced.
prediction_insert = '''INSERT INTO predictions(prediction_id,device_id,image_path,
    model_id,top_class_id,confidence,second_class_id,second_confidence)
    VALUES (?, 'anon-test', 'test.jpg', ?, 1, ?, 2, ?)'''
db.execute(prediction_insert, ('threshold', 'model-demo-1', 0.8, 0.1))
assert scalar("SELECT status FROM predictions WHERE prediction_id='threshold'") == 'confident'
assert scalar("SELECT count(*) FROM escalations WHERE prediction_id='threshold'") == 0
assert scalar("SELECT count(*) FROM sync_queue WHERE row_id='threshold'") == 1
db.execute(prediction_insert, ('tie', 'model-demo-3', 0.5, 0.5))
assert scalar("SELECT reason FROM escalations WHERE prediction_id='tie'") == 'close_scores'
rejected(prediction_insert, ('bad-fk', 'missing-model', 0.8, 0.1))
rejected(prediction_insert, ('bad-probability', 'model-demo-1', 0.8, 0.4))

# A resolution after acknowledgement must requeue the prediction envelope.
db.execute("DELETE FROM sync_queue WHERE row_id='pred-demo-1'")
db.execute("""UPDATE predictions SET synced=1,synced_at='2026-10-03T11:00:00.000Z'
              WHERE prediction_id='pred-demo-1'""")
db.execute("""UPDATE escalations SET resolved=1,resolved_at='2026-10-03T12:00:00.000Z'
              WHERE prediction_id='pred-demo-1'""")
assert scalar("SELECT synced FROM predictions WHERE prediction_id='pred-demo-1'") == 0
assert scalar("SELECT count(*) FROM sync_queue WHERE row_id='pred-demo-1'") == 1

# Validate JSON on an unused model, avoiding the immutability guard.
model_insert = '''INSERT INTO model_versions(model_id,version,base_model,quantization,
    file_size_mb,confidence_threshold,accuracy_by_test_set)
    VALUES ('json-test','json-test','demo','int8',1,0.8,?)'''
for bad in ('not-json', '{}', '[]',
            '{"jmuben_test":1.1,"own_photos":null,"plantdoc_ood":null}',
            '{"jmuben_test":"high","own_photos":null,"plantdoc_ood":null}'):
    rejected(model_insert, (bad,))
db.execute(model_insert, ('{"jmuben_test":0.8,"own_photos":null,"plantdoc_ood":null}',))
rejected("""INSERT INTO voice_phrases VALUES
    ('cv','hi','test','cv.mp3','common_voice','release license',1)""")
db.execute("""INSERT INTO voice_phrases VALUES
    ('cv','hi','test','cv.mp3','common_voice','release license',NULL)""")
assert scalar("SELECT count(*) FROM playback_phrases WHERE phrase_id='cv'") == 0
assert not db.execute('PRAGMA foreign_key_check').fetchall()
print(f'PASS: SQLite {sqlite3.sqlite_version}; seeds, leakage, role restrictions, review, '
      'prediction decisions, escalation, requeue, JSON, counts and foreign keys.')
