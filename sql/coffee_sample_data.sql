-- DEMONSTRATION DATA ONLY: paths/hashes/model sizes are fixtures, not real assets.
-- No field study, human review, speech recording, or model evaluation is claimed.
-- Run after coffee_schema.sql in a fresh database.
PRAGMA foreign_keys = ON;
BEGIN;

INSERT INTO datasets
 (dataset_id,name,doi,source_url,license,size_mb,version,citation,role,
  preprocessing_notes,what_it_does_not_cover)
VALUES
 ('jmuben','JMuBEN','10.17632/t2r6rszp5c.1',
  'https://data.mendeley.com/datasets/t2r6rszp5c/1','CC-BY-4.0',NULL,'1',
  'Jepkoech et al. (2021), JMuBEN, Mendeley Data, V1, doi:10.17632/t2r6rszp5c.1',
  'train','Kenyan Arabica; cropped, center-squared; rotation/flip augmentation. Rust, Cercospora, Phoma. Deduplicate and group before splitting.',
  'No demonstrated Indian field performance; limited backgrounds, species and disorders.'),
 ('jmuben2','JMuBEN2','10.17632/tgv3zb82nd.1',
  'https://data.mendeley.com/datasets/tgv3zb82nd/1','CC-BY-4.0',NULL,'1',
  'Jepkoech et al. (2021), JMuBEN2, Mendeley Data, V1, doi:10.17632/tgv3zb82nd.1',
  'train','Kenyan Arabica; cropped, center-squared; rotation/flip augmentation. Healthy and Miner. Deduplicate and group before splitting.',
  'Does not establish robustness to uncropped phone photos or other pests.'),
 ('plantdoc','PlantDoc',NULL,NULL,NULL,NULL,NULL,
  'PlantDoc; optional import; pin source, release, citation and license before use.',
  'ood_test','OOD evaluation only. Keep test-only; audit overlap with training groups. Map out-of-taxonomy images to unknown.',
  'Not a coffee training source; OOD accuracy requires an explicit label/rejection protocol.'),
 ('own_photos','own_photos',NULL,NULL,NULL,NULL,'pilot-1',
  'Project-owned phone-photo pilot; planned 20-30 leaves; collection and labelling pending.',
  'test','Real-world held-out photos. Preserve field appearance; strip metadata, label independently, group repeated photos of a leaf.',
  'Small convenience sample, not evidence of population-wide diagnostic accuracy.'),
 ('common_voice_hi','common_voice_hi',NULL,NULL,NULL,NULL,NULL,
  'Mozilla Common Voice, Hindi; pin downloaded release, license and citation before use.',
  'voice','Speech recognition clips/transcripts only. Retain official speech split manifest outside this image catalog.',
  'No leaf labels, no guaranteed agricultural vocabulary, and not a synthesized-voice source.');

-- Six rows are necessary: five named categories plus unknown.
-- image_count starts at zero and is maintained by image triggers.
INSERT INTO classes(class_id,label_en,label_hi,type) VALUES
 (1,'Rust','कॉफी रतुआ','disease'),
 (2,'Cercospora','सर्कोस्पोरा पत्ती धब्बा','disease'),
 (3,'Phoma','फोमा','disease'),
 (4,'Miner','पत्ती सुरंगक कीट','pest'),
 (5,'Healthy','स्वस्थ','healthy'),
 (6,'unknown','अज्ञात / निश्चित नहीं','unknown');

-- local_folder_name is relative to jmuben/; spelling preserves source terminology.
INSERT INTO label_map(dataset_id,local_folder_name,source_class_name,class_id) VALUES
 ('jmuben','Rust','Rust',1),
 ('jmuben','Cercospora','Cescospora',2),
 ('jmuben','Phoma','Phoma',3),
 ('jmuben2','Miner','Miner',4),
 ('jmuben2','Healthy','healthy',5);

-- Illustrative dimensions and hashes; never ingest these as real measurements.
-- Flags describe the stored asset: cropped source images are not field-test photos.
INSERT INTO images
 (image_id,file_path,sha256,phash,width,height,dataset_id,class_id,source_group_id,
  split,is_augmented,is_field_photo,is_synthetic,is_own_photo)
VALUES
 ('img-demo-1','jmuben/Rust/demo_01.jpg',
  '1111111111111111111111111111111111111111111111111111111111111111',
  '0123456789abcdef',224,224,'jmuben',1,'leaf-demo-1','train',0,0,0,0),
 ('img-demo-2','jmuben/Cercospora/demo_02.jpg',
  '2222222222222222222222222222222222222222222222222222222222222222',
  '123456789abcdef0',224,224,'jmuben',2,'leaf-demo-2','val',1,0,0,0),
 ('img-demo-3','jmuben/Phoma/demo_03.jpg',
  '3333333333333333333333333333333333333333333333333333333333333333',
  '23456789abcdef01',224,224,'jmuben',3,'leaf-demo-3','test',0,0,0,0),
 ('img-demo-4','jmuben/Miner/demo_04.jpg',
  '4444444444444444444444444444444444444444444444444444444444444444',
  '3456789abcdef012',224,224,'jmuben2',4,'leaf-demo-4','train',1,0,0,0),
 ('img-demo-5','jmuben/Healthy/demo_05.jpg',
  '5555555555555555555555555555555555555555555555555555555555555555',
  '456789abcdef0123',224,224,'jmuben2',5,'leaf-demo-5','test',0,0,0,0);

-- Unreviewed drafts: reviewed_advice intentionally returns NO rows after seeding.
INSERT INTO advice
 (advice_id,class_id,language,advice_text,action_steps,source_citation,
  reviewed_by_human,reviewer,review_date)
VALUES
 ('advice-demo-1',1,'hi','मसौदा: स्थानीय कृषि सलाहकार से पुष्टि कराएँ।',
  'साफ तस्वीर लें। सलाहकार को पत्ती दिखाएँ।','DEMO draft; authoritative agronomy citation pending.',0,NULL,NULL),
 ('advice-demo-2',2,'hi','मसौदा: स्थानीय कृषि सलाहकार से पुष्टि कराएँ।',
  'साफ तस्वीर लें। सलाहकार को पत्ती दिखाएँ।','DEMO draft; authoritative agronomy citation pending.',0,NULL,NULL),
 ('advice-demo-3',3,'hi','मसौदा: स्थानीय कृषि सलाहकार से पुष्टि कराएँ।',
  'साफ तस्वीर लें। सलाहकार को पत्ती दिखाएँ।','DEMO draft; authoritative agronomy citation pending.',0,NULL,NULL),
 ('advice-demo-4',4,'hi','मसौदा: स्थानीय कृषि सलाहकार से पुष्टि कराएँ।',
  'साफ तस्वीर लें। सलाहकार को पत्ती दिखाएँ।','DEMO draft; authoritative agronomy citation pending.',0,NULL,NULL),
 ('advice-demo-5',5,'hi','मसौदा: तस्वीर का परिणाम अंतिम पुष्टि नहीं है।',
  'चिंता होने पर सलाहकार से संपर्क करें।','DEMO draft; authoritative agronomy citation pending.',0,NULL,NULL);

-- Planned, project-owned UI recordings; no actual audio is supplied.
-- Common Voice transcripts must be imported verbatim from the pinned release.
INSERT INTO voice_phrases
 (phrase_id,language,text,audio_path,audio_source,audio_license,linked_class_id)
VALUES
 ('voice-demo-1','hi','पत्ती की तस्वीर लें।','audio/demo/capture.wav','own_recording','DEMO: project recording rights pending',NULL),
 ('voice-demo-2','hi','तस्वीर साफ नहीं है।','audio/demo/blur.wav','own_recording','DEMO: project recording rights pending',NULL),
 ('voice-demo-3','hi','कृपया फिर से कोशिश करें।','audio/demo/retry.wav','own_recording','DEMO: project recording rights pending',NULL),
 ('voice-demo-4','hi','परिणाम निश्चित नहीं है।','audio/demo/uncertain.wav','own_recording','DEMO: project recording rights pending',6),
 ('voice-demo-5','hi','तस्वीर फोन पर सहेजी गई है।','audio/demo/saved.wav','own_recording','DEMO: project recording rights pending',NULL);

-- Sizes and thresholds below are demo parameters, not evaluated model claims.
-- JSON null means not measured; it must never be reported as zero accuracy.
INSERT INTO model_versions
 (model_id,version,base_model,quantization,file_size_mb,confidence_threshold,
  min_margin,accuracy_by_test_set,created_at)
VALUES
 ('model-demo-1','demo-1','MobileNetV3Small','int8',3.0,0.80,0.10,
  '{"jmuben_test":null,"own_photos":null,"plantdoc_ood":null}','2026-10-03T00:00:00.000Z'),
 ('model-demo-2','demo-2','MobileNetV3Small','int8',3.1,0.75,0.10,
  '{"jmuben_test":null,"own_photos":null,"plantdoc_ood":null}','2026-10-03T00:00:00.000Z'),
 ('model-demo-3','demo-3','MobileNetV3Small','int8',3.2,0.45,0.10,
  '{"jmuben_test":null,"own_photos":null,"plantdoc_ood":null}','2026-10-03T00:00:00.000Z'),
 ('model-demo-4','demo-4','MobileNetV3Small','float16',6.0,0.80,0.10,
  '{"jmuben_test":null,"own_photos":null,"plantdoc_ood":null}','2026-10-03T00:00:00.000Z'),
 ('model-demo-5','demo-5','MobileNetV3Small','int8',3.3,0.85,0.10,
  '{"jmuben_test":null,"own_photos":null,"plantdoc_ood":null}','2026-10-03T00:00:00.000Z');

-- Five uncertain cases generate exactly five escalations and five queue entries.
-- The trigger calculates status regardless of a caller-provided initial value.
INSERT INTO predictions
 (prediction_id,device_id,timestamp,image_path,model_id,top_class_id,confidence,
  second_class_id,second_confidence,status,synced,synced_at)
VALUES
 ('pred-demo-1','anon-demo-a','2026-10-03T10:00:00.000Z','jmuben/Rust/demo_01.jpg',
  'model-demo-1',1,0.65,2,0.20,'confident',0,NULL),
 ('pred-demo-2','anon-demo-a','2026-10-03T10:01:00.000Z','jmuben/Cercospora/demo_02.jpg',
  'model-demo-2',2,0.60,3,0.25,'confident',0,NULL),
 ('pred-demo-3','anon-demo-b','2026-10-03T10:02:00.000Z','jmuben/Phoma/demo_03.jpg',
  'model-demo-3',3,0.50,1,0.45,'confident',0,NULL),
 ('pred-demo-4','anon-demo-b','2026-10-03T10:03:00.000Z','jmuben/Miner/demo_04.jpg',
  'model-demo-4',6,0.90,4,0.05,'confident',0,NULL),
 ('pred-demo-5','anon-demo-c','2026-10-03T10:04:00.000Z','jmuben/Healthy/demo_05.jpg',
  'model-demo-5',5,0.55,1,0.30,'confident',0,NULL);

-- Explicit five-row escalation fixture: these already exist via the trigger.
-- NOT EXISTS makes the example non-duplicating without suppressing constraints.
WITH sample(escalation_id,prediction_id,reason,routed_to,resolved,resolved_at) AS (
 VALUES
 ('auto:pred-demo-1','pred-demo-1','low_confidence',NULL,0,NULL),
 ('auto:pred-demo-2','pred-demo-2','low_confidence',NULL,0,NULL),
 ('auto:pred-demo-3','pred-demo-3','close_scores',NULL,0,NULL),
 ('auto:pred-demo-4','pred-demo-4','unknown_input',NULL,0,NULL),
 ('auto:pred-demo-5','pred-demo-5','low_confidence',NULL,0,NULL)
)
INSERT INTO escalations(escalation_id,prediction_id,reason,routed_to,resolved,resolved_at)
SELECT * FROM sample s WHERE NOT EXISTS (
 SELECT 1 FROM escalations e WHERE e.prediction_id=s.prediction_id);

-- Likewise, these five queue entries were created atomically by the triggers.
WITH sample(queue_id,table_name,row_id,created_at,attempts,last_error) AS (
 VALUES
 (1,'predictions','pred-demo-1','2026-10-03T10:00:00.000Z',0,NULL),
 (2,'predictions','pred-demo-2','2026-10-03T10:01:00.000Z',0,NULL),
 (3,'predictions','pred-demo-3','2026-10-03T10:02:00.000Z',0,NULL),
 (4,'predictions','pred-demo-4','2026-10-03T10:03:00.000Z',0,NULL),
 (5,'predictions','pred-demo-5','2026-10-03T10:04:00.000Z',0,NULL)
)
INSERT INTO sync_queue(queue_id,table_name,row_id,created_at,attempts,last_error)
SELECT * FROM sample s WHERE NOT EXISTS (
 SELECT 1 FROM sync_queue q WHERE q.row_id=s.row_id);

COMMIT;
