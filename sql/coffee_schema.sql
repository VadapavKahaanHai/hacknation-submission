-- Requires SQLite with JSON1. Run on every connection, before a transaction.
PRAGMA foreign_keys = ON;
PRAGMA user_version = 2;

-- Build provenance and a portable relative path to the external media root.
CREATE TABLE database_metadata (
  key TEXT PRIMARY KEY NOT NULL,
  value TEXT NOT NULL
);

CREATE TABLE datasets (
  dataset_id TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL UNIQUE,
  doi TEXT UNIQUE,
  source_url TEXT,
  license TEXT,                         -- NULL means not verified yet
  size_mb REAL CHECK (size_mb >= 0),     -- actual local import size
  version TEXT,                        -- pin the release actually downloaded
  citation TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('train','test','ood_test','voice')),
  preprocessing_notes TEXT NOT NULL,
  what_it_does_not_cover TEXT NOT NULL,
  CHECK (name <> 'PlantDoc' OR role = 'ood_test'),
  CHECK (name <> 'own_photos' OR role = 'test'),
  CHECK (name <> 'common_voice_hi' OR role = 'voice')
);

CREATE TABLE classes (
  class_id INTEGER PRIMARY KEY,
  label_en TEXT NOT NULL UNIQUE,
  label_hi TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('disease','pest','healthy','unknown')),
  image_count INTEGER NOT NULL DEFAULT 0 CHECK (image_count >= 0)
);

CREATE TABLE label_map (
  dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
  local_folder_name TEXT NOT NULL,
  source_class_name TEXT NOT NULL,
  class_id INTEGER NOT NULL REFERENCES classes(class_id),
  PRIMARY KEY (dataset_id, local_folder_name)
);

CREATE TABLE images (
  image_id TEXT PRIMARY KEY NOT NULL,    -- app-generated UUID in production
  file_path TEXT NOT NULL UNIQUE,        -- relative to private app media root
  sha256 TEXT NOT NULL UNIQUE
    CHECK (length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
  phash TEXT NOT NULL
    CHECK (length(phash)=16 AND phash NOT GLOB '*[^0-9a-f]*'),
  width INTEGER NOT NULL CHECK (width > 0),
  height INTEGER NOT NULL CHECK (height > 0),
  dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
  class_id INTEGER NOT NULL REFERENCES classes(class_id),
  source_group_id TEXT NOT NULL CHECK (length(source_group_id)>0),
  split TEXT NOT NULL CHECK (split IN ('train','val','test')),
  is_augmented INTEGER CHECK (is_augmented IN (0,1)), -- NULL = source provenance unknown
  is_field_photo INTEGER NOT NULL DEFAULT 0 CHECK (is_field_photo IN (0,1)),
  is_synthetic INTEGER NOT NULL DEFAULT 0 CHECK (is_synthetic IN (0,1)),
  is_own_photo INTEGER NOT NULL DEFAULT 0 CHECK (is_own_photo IN (0,1)),
  CHECK (is_synthetic=0 OR (is_field_photo=0 AND is_own_photo=0)),
  CHECK (is_own_photo=0 OR (is_field_photo=1 AND split='test')),
  CHECK (dataset_id <> 'own_photos' OR
         (is_own_photo=1 AND is_augmented IS 0 AND is_synthetic=0))
);

CREATE TABLE advice (
  advice_id TEXT PRIMARY KEY NOT NULL,
  class_id INTEGER NOT NULL REFERENCES classes(class_id),
  language TEXT NOT NULL CHECK (language IN ('en','hi')),
  advice_text TEXT NOT NULL,
  action_steps TEXT NOT NULL,            -- plain text, one step per line
  source_citation TEXT NOT NULL,
  reviewed_by_human INTEGER NOT NULL DEFAULT 0 CHECK (reviewed_by_human IN (0,1)),
  reviewer TEXT,                        -- staff identifier, not farmer data
  review_date TEXT,                     -- UTC ISO 8601
  symptoms TEXT NOT NULL DEFAULT '',
  treatment TEXT NOT NULL DEFAULT '',
  prevention TEXT NOT NULL DEFAULT '',
  when_to_escalate TEXT NOT NULL DEFAULT '',
  UNIQUE (class_id, language),
  CHECK (reviewed_by_human=0 OR
    (reviewer IS NOT NULL AND length(trim(reviewer))>0 AND review_date IS NOT NULL))
);

CREATE TABLE voice_phrases (
  phrase_id TEXT PRIMARY KEY NOT NULL,
  language TEXT NOT NULL CHECK (language IN ('en','hi')),
  text TEXT NOT NULL,
  audio_path TEXT NOT NULL UNIQUE,
  audio_source TEXT NOT NULL CHECK (audio_source IN ('common_voice','tts','own_recording')),
  audio_license TEXT NOT NULL,
  linked_class_id INTEGER REFERENCES classes(class_id),
  CHECK (audio_source <> 'common_voice' OR
         (language='hi' AND linked_class_id IS NULL))
);

CREATE TABLE model_versions (
  model_id TEXT PRIMARY KEY NOT NULL,
  version TEXT NOT NULL UNIQUE,
  base_model TEXT NOT NULL,
  quantization TEXT NOT NULL CHECK (quantization IN ('int8','float16','float32')),
  file_size_mb REAL NOT NULL CHECK (file_size_mb>0),
  confidence_threshold REAL NOT NULL CHECK (confidence_threshold BETWEEN 0 AND 1),
  min_margin REAL NOT NULL DEFAULT 0.10 CHECK (min_margin>0 AND min_margin<=1),
  accuracy_by_test_set TEXT NOT NULL
    DEFAULT '{"jmuben_test":null,"own_photos":null,"plantdoc_ood":null}'
    CHECK (CASE WHEN json_valid(accuracy_by_test_set) THEN
      json_type(accuracy_by_test_set)='object'
      AND coalesce(json_type(accuracy_by_test_set,'$.jmuben_test') IN ('null','real','integer'),0)
      AND coalesce(json_type(accuracy_by_test_set,'$.own_photos') IN ('null','real','integer'),0)
      AND coalesce(json_type(accuracy_by_test_set,'$.plantdoc_ood') IN ('null','real','integer'),0)
      AND (json_extract(accuracy_by_test_set,'$.jmuben_test') IS NULL OR
           json_extract(accuracy_by_test_set,'$.jmuben_test') BETWEEN 0 AND 1)
      AND (json_extract(accuracy_by_test_set,'$.own_photos') IS NULL OR
           json_extract(accuracy_by_test_set,'$.own_photos') BETWEEN 0 AND 1)
      AND (json_extract(accuracy_by_test_set,'$.plantdoc_ood') IS NULL OR
           json_extract(accuracy_by_test_set,'$.plantdoc_ood') BETWEEN 0 AND 1)
      ELSE 0 END),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE predictions (
  prediction_id TEXT PRIMARY KEY NOT NULL, -- random UUID; stable across retries
  device_id TEXT NOT NULL,                 -- random installation ID, no hardware ID
  timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  image_path TEXT NOT NULL,
  model_id TEXT NOT NULL REFERENCES model_versions(model_id),
  top_class_id INTEGER NOT NULL REFERENCES classes(class_id),
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  second_class_id INTEGER NOT NULL REFERENCES classes(class_id),
  second_confidence REAL NOT NULL CHECK (second_confidence BETWEEN 0 AND 1),
  status TEXT NOT NULL DEFAULT 'not_sure_ask_a_person'
    CHECK (status IN ('confident','not_sure_ask_a_person')),
  synced INTEGER NOT NULL DEFAULT 0 CHECK (synced IN (0,1)),
  synced_at TEXT,
  CHECK (top_class_id <> second_class_id),
  CHECK (confidence >= second_confidence),
  CHECK (confidence + second_confidence <= 1.000001),
  CHECK ((synced=0 AND synced_at IS NULL) OR (synced=1 AND synced_at IS NOT NULL))
);

CREATE TABLE escalations (
  escalation_id TEXT PRIMARY KEY NOT NULL,
  prediction_id TEXT NOT NULL UNIQUE REFERENCES predictions(prediction_id),
  reason TEXT NOT NULL CHECK (reason IN
    ('unknown_input','low_confidence','close_scores','review_requested')),
  routed_to TEXT,                          -- service/role code, never personal contact
  resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0,1)),
  resolved_at TEXT,
  CHECK ((resolved=0 AND resolved_at IS NULL) OR
         (resolved=1 AND resolved_at IS NOT NULL))
);

CREATE TABLE sync_queue (
  queue_id INTEGER PRIMARY KEY,
  -- Each entry sends a prediction AND its optional escalation as one envelope.
  -- Restricting the target allows a real FK instead of a polymorphic pseudo-FK.
  table_name TEXT NOT NULL DEFAULT 'predictions' CHECK (table_name='predictions'),
  row_id TEXT NOT NULL UNIQUE REFERENCES predictions(prediction_id),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts>=0),
  last_error TEXT                          -- sanitized error code, no payload/PII
);

CREATE INDEX label_map_class_idx ON label_map(class_id);
CREATE INDEX images_split_idx ON images(split);
CREATE INDEX images_class_idx ON images(class_id);
CREATE INDEX images_dataset_class_split_idx ON images(dataset_id,class_id,split);
CREATE INDEX images_group_split_idx ON images(source_group_id,split);
CREATE INDEX voice_class_idx ON voice_phrases(linked_class_id);
CREATE INDEX predictions_model_idx ON predictions(model_id);
CREATE INDEX predictions_class_idx ON predictions(top_class_id);
CREATE INDEX predictions_second_class_idx ON predictions(second_class_id);
CREATE INDEX predictions_synced_idx ON predictions(synced,timestamp);
CREATE INDEX predictions_status_idx ON predictions(status,timestamp);
CREATE INDEX queue_created_idx ON sync_queue(created_at);

-- This view includes zero counts, and excludes speech-only datasets.
CREATE VIEW class_image_counts AS
SELECT d.dataset_id,d.name AS dataset,c.class_id,c.label_en,s.split,
       count(i.image_id) AS image_count,
       count(DISTINCT i.source_group_id) AS source_group_count
FROM datasets d CROSS JOIN classes c
CROSS JOIN (SELECT 'train' AS split UNION ALL SELECT 'val' UNION ALL SELECT 'test') s
LEFT JOIN images i ON i.dataset_id=d.dataset_id AND i.class_id=c.class_id AND i.split=s.split
WHERE d.role <> 'voice'
GROUP BY d.dataset_id,d.name,c.class_id,c.label_en,s.split;

CREATE VIEW split_leakage AS
SELECT source_group_id,count(DISTINCT split) AS split_count
FROM images GROUP BY source_group_id HAVING count(DISTINCT split)>1;

-- The application UI must read this view, never the raw advice table.
CREATE VIEW reviewed_advice AS
SELECT advice_id,class_id,language,advice_text,action_steps,source_citation,reviewer,review_date,
       symptoms,treatment,prevention,when_to_escalate
FROM advice WHERE reviewed_by_human=1;

-- Common Voice is speech-recognition data only; it is excluded from playback.
CREATE VIEW playback_phrases AS
SELECT * FROM voice_phrases WHERE audio_source IN ('tts','own_recording');

CREATE VIEW prediction_decisions AS
SELECT p.prediction_id,
 CASE WHEN c.type='unknown' THEN 'unknown_input'
      WHEN p.confidence<m.confidence_threshold THEN 'low_confidence'
      WHEN p.confidence-p.second_confidence<m.min_margin THEN 'close_scores'
      ELSE NULL END AS reason
FROM predictions p JOIN model_versions m ON m.model_id=p.model_id
JOIN classes c ON c.class_id=p.top_class_id;

-- Preferred advisory lookup: uncertain predictions cannot return class advice.
CREATE VIEW prediction_advice AS
SELECT p.prediction_id,a.* FROM predictions p
JOIN reviewed_advice a ON a.class_id=p.top_class_id
WHERE p.status='confident';

CREATE TRIGGER images_validate_insert BEFORE INSERT ON images BEGIN
  SELECT RAISE(ABORT,'source group spans splits') WHERE EXISTS (
    SELECT 1 FROM images WHERE source_group_id=NEW.source_group_id AND split<>NEW.split);
  SELECT RAISE(ABORT,'dataset role forbids this image split') WHERE EXISTS (
    SELECT 1 FROM datasets WHERE dataset_id=NEW.dataset_id AND
      (role='voice' OR (role IN ('test','ood_test') AND NEW.split<>'test')));
END;
CREATE TRIGGER images_validate_update BEFORE UPDATE OF dataset_id,source_group_id,split ON images BEGIN
  SELECT RAISE(ABORT,'source group spans splits') WHERE EXISTS (
    SELECT 1 FROM images WHERE image_id<>OLD.image_id
      AND source_group_id=NEW.source_group_id AND split<>NEW.split);
  SELECT RAISE(ABORT,'dataset role forbids this image split') WHERE EXISTS (
    SELECT 1 FROM datasets WHERE dataset_id=NEW.dataset_id AND
      (role='voice' OR (role IN ('test','ood_test') AND NEW.split<>'test')));
END;
CREATE TRIGGER dataset_role_immutable BEFORE UPDATE OF role ON datasets
WHEN EXISTS (SELECT 1 FROM images WHERE dataset_id=OLD.dataset_id)
BEGIN SELECT RAISE(ABORT,'dataset role is frozen after import'); END;

-- image_count is a cache of imported, deduplicated file rows, not published totals.
CREATE TRIGGER images_count_insert AFTER INSERT ON images BEGIN
  UPDATE classes SET image_count=image_count+1 WHERE class_id=NEW.class_id;
END;
CREATE TRIGGER images_count_delete AFTER DELETE ON images BEGIN
  UPDATE classes SET image_count=image_count-1 WHERE class_id=OLD.class_id;
END;
CREATE TRIGGER images_count_update AFTER UPDATE OF class_id ON images
WHEN NEW.class_id<>OLD.class_id BEGIN
  UPDATE classes SET image_count=image_count-1 WHERE class_id=OLD.class_id;
  UPDATE classes SET image_count=image_count+1 WHERE class_id=NEW.class_id;
END;

-- Edited content requires a fresh human review, even if already approved.
CREATE TRIGGER advice_edit_unreview AFTER UPDATE OF class_id,language,advice_text,action_steps,source_citation,
 symptoms,treatment,prevention,when_to_escalate ON advice
BEGIN
  UPDATE advice SET reviewed_by_human=0,reviewer=NULL,review_date=NULL
  WHERE advice_id=NEW.advice_id;
END;

-- Prediction evidence is append-only; reruns are new prediction IDs.
CREATE TRIGGER prediction_evidence_immutable
BEFORE UPDATE OF prediction_id,device_id,timestamp,image_path,model_id,
 top_class_id,confidence,second_class_id,second_confidence ON predictions
BEGIN SELECT RAISE(ABORT,'prediction evidence is immutable'); END;
CREATE TRIGGER model_used_immutable BEFORE UPDATE ON model_versions
WHEN EXISTS (SELECT 1 FROM predictions WHERE model_id=OLD.model_id)
BEGIN SELECT RAISE(ABORT,'used model version is immutable; create a new version'); END;
CREATE TRIGGER class_semantics_immutable BEFORE UPDATE OF class_id,label_en,type ON classes
BEGIN SELECT RAISE(ABORT,'class semantics are immutable'); END;

CREATE TRIGGER prediction_status_guard BEFORE UPDATE OF status ON predictions
WHEN NEW.status<>(SELECT CASE WHEN reason IS NULL THEN 'confident'
  ELSE 'not_sure_ask_a_person' END FROM prediction_decisions WHERE prediction_id=OLD.prediction_id)
BEGIN SELECT RAISE(ABORT,'status must match model decision'); END;

-- A single trigger makes status, escalation, and outbox insertion atomic.
CREATE TRIGGER prediction_created AFTER INSERT ON predictions BEGIN
  UPDATE predictions SET status=(SELECT CASE WHEN reason IS NULL THEN 'confident'
    ELSE 'not_sure_ask_a_person' END FROM prediction_decisions
    WHERE prediction_id=NEW.prediction_id) WHERE prediction_id=NEW.prediction_id;
  INSERT INTO escalations(escalation_id,prediction_id,reason)
    SELECT 'auto:'||NEW.prediction_id,NEW.prediction_id,reason
    FROM prediction_decisions WHERE prediction_id=NEW.prediction_id AND reason IS NOT NULL;
  INSERT INTO sync_queue(table_name,row_id)
    SELECT 'predictions',NEW.prediction_id
    WHERE NOT EXISTS (SELECT 1 FROM sync_queue WHERE row_id=NEW.prediction_id);
END;

CREATE TRIGGER escalation_identity_immutable BEFORE UPDATE OF escalation_id,prediction_id,reason ON escalations
BEGIN SELECT RAISE(ABORT,'escalation identity and reason are immutable'); END;
CREATE TRIGGER escalation_created AFTER INSERT ON escalations BEGIN
  UPDATE predictions SET synced=0,synced_at=NULL WHERE prediction_id=NEW.prediction_id;
  INSERT INTO sync_queue(table_name,row_id)
    SELECT 'predictions',NEW.prediction_id
    WHERE NOT EXISTS (SELECT 1 FROM sync_queue WHERE row_id=NEW.prediction_id);
END;
CREATE TRIGGER escalation_changed AFTER UPDATE OF routed_to,resolved,resolved_at ON escalations BEGIN
  UPDATE predictions SET synced=0,synced_at=NULL WHERE prediction_id=NEW.prediction_id;
  INSERT INTO sync_queue(table_name,row_id)
    SELECT 'predictions',NEW.prediction_id
    WHERE NOT EXISTS (SELECT 1 FROM sync_queue WHERE row_id=NEW.prediction_id);
END;
