-- crop_db.sql  (schema_version 2, content 0.3.0: crop set C = bean + coffee + maize)
-- Build:  python3 cropdb.py build crop.db
-- Fallback if coffee data fails:  python3 cropdb.py set-cropset crop.db bean_maize
-- Schema is IDENTICAL to Sprint 1. Only seed content changed.
-- symptom_id and advice_id are now explicit so translations can never point at the wrong row.

PRAGMA foreign_keys = ON;

------------------------------------------------------------
-- SCHEMA (unchanged from Sprint 1)
------------------------------------------------------------

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE languages (
    lang       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    script     TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE crops (
    crop_id         TEXT PRIMARY KEY,
    name_en         TEXT NOT NULL,
    scientific_name TEXT,
    icon            TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    sort_order      INTEGER NOT NULL DEFAULT 0,
    CHECK (crop_id NOT GLOB '*[^a-z_]*')
);

CREATE TABLE urgency_levels (
    level     INTEGER PRIMARY KEY CHECK (level BETWEEN 0 AND 3),
    label_en  TEXT NOT NULL,
    action_en TEXT NOT NULL,
    color     TEXT NOT NULL
);

CREATE TABLE diseases (
    disease_id     TEXT PRIMARY KEY,
    crop_id        TEXT REFERENCES crops(crop_id),
    name_en        TEXT NOT NULL,
    pathogen       TEXT,
    cause_type     TEXT NOT NULL CHECK (cause_type IN
                     ('fungal','bacterial','viral','oomycete','pest','none','unknown')),
    urgency        INTEGER NOT NULL REFERENCES urgency_levels(level),
    is_curable     INTEGER NOT NULL CHECK (is_curable IN (0,1)),
    see_expert     INTEGER NOT NULL DEFAULT 0 CHECK (see_expert IN (0,1)),
    summary_en     TEXT NOT NULL,
    why_en         TEXT NOT NULL,
    content_status TEXT NOT NULL DEFAULT 'draft' CHECK (content_status IN ('draft','reviewed')),
    source         TEXT NOT NULL,
    CHECK (disease_id NOT GLOB '*[^a-z0-9_]*'),
    CHECK (crop_id IS NULL OR disease_id GLOB crop_id || '_*')
);

CREATE TABLE symptoms (
    symptom_id INTEGER PRIMARY KEY,
    disease_id TEXT NOT NULL REFERENCES diseases(disease_id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    plant_part TEXT NOT NULL CHECK (plant_part IN ('leaf','stem','root','fruit','pod','whole_plant')),
    text_en    TEXT NOT NULL
);

CREATE TABLE advice (
    advice_id   INTEGER PRIMARY KEY,
    disease_id  TEXT NOT NULL REFERENCES diseases(disease_id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('today','treatment','prevention')),
    method      TEXT NOT NULL CHECK (method IN ('cultural','chemical','biological','varietal')),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    text_en     TEXT NOT NULL,
    safety_note TEXT
);

CREATE TRIGGER chemical_needs_safety_note
BEFORE INSERT ON advice
WHEN NEW.method = 'chemical' AND COALESCE(NEW.safety_note,'') = ''
BEGIN SELECT RAISE(ABORT, 'chemical advice requires a safety_note'); END;

CREATE TABLE followup_questions (
    question_id TEXT PRIMARY KEY,
    crop_id     TEXT NOT NULL REFERENCES crops(crop_id),
    disease_id  TEXT NOT NULL REFERENCES diseases(disease_id),
    text_en     TEXT NOT NULL,
    yes_weight  REAL NOT NULL CHECK (yes_weight > 0),
    no_weight   REAL NOT NULL CHECK (no_weight > 0)
);

CREATE TABLE translations (
    entity_type   TEXT NOT NULL CHECK (entity_type IN
                    ('crop','disease','symptom','advice','question','urgency','ui')),
    entity_id     TEXT NOT NULL,
    field         TEXT NOT NULL,
    lang          TEXT NOT NULL REFERENCES languages(lang),
    text          TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'unverified'
                    CHECK (review_status IN ('unverified','native_reviewed','expert_reviewed')),
    PRIMARY KEY (entity_type, entity_id, field, lang)
);

CREATE TABLE ui_strings (
    key     TEXT PRIMARY KEY,
    text_en TEXT NOT NULL
);

CREATE TABLE label_aliases (
    source     TEXT NOT NULL,
    raw_label  TEXT NOT NULL,
    disease_id TEXT REFERENCES diseases(disease_id),
    PRIMARY KEY (source, raw_label)
);

CREATE TABLE model_classes (
    model_version TEXT NOT NULL,
    class_index   INTEGER NOT NULL,
    disease_id    TEXT NOT NULL REFERENCES diseases(disease_id),
    PRIMARY KEY (model_version, class_index),
    UNIQUE (model_version, disease_id)
);

CREATE INDEX idx_diseases_crop ON diseases(crop_id);
CREATE INDEX idx_symptoms_dis  ON symptoms(disease_id, sort_order);
CREATE INDEX idx_advice_dis    ON advice(disease_id, kind, sort_order);
CREATE INDEX idx_tr_lookup     ON translations(lang, entity_type, entity_id);

------------------------------------------------------------
-- SEED  (all content 'draft' until an agronomist signs off)
------------------------------------------------------------

INSERT INTO meta VALUES
 ('schema_version','2'),
 ('content_version','0.4.2'),
 ('cropset','full'),
 ('active_model_version','v0.3'),
 ('threshold_high','0.90'),
 ('threshold_unknown','0.40'),
 ('threshold_margin','0.15'),
 ('threshold_danger_watch','0.25');

INSERT INTO languages VALUES
 ('en','English','Latn',1,1),
 ('hi','हिन्दी','Deva',1,2),
 ('pa','ਪੰਜਾਬੀ','Guru',1,3);

INSERT INTO crops VALUES
 ('bean','Bean','Phaseolus vulgaris','bean.png',1,1),
 ('coffee','Coffee','Coffea arabica / Coffea canephora','coffee.png',1,2),
 ('maize','Maize','Zea mays','maize.png',1,3);

INSERT INTO urgency_levels VALUES
 (0,'None','No action needed','#2E7D32'),
 (1,'Low','Watch the crop, act within 2 weeks','#9E9D24'),
 (2,'Medium','Act this week','#EF6C00'),
 (3,'High','Act today','#C62828');

INSERT INTO ui_strings VALUES
 ('disclaimer','This is a guess by a computer, not a test. Before spraying anything, check with your local agriculture officer or agro dealer.'),
 ('retake','We could not read this photo. Take a new photo of one leaf, close up, in daylight.'),
 ('see_expert','Show this leaf to an agriculture officer soon.'),
 ('uncertain','We are not sure. Please answer one or two questions.'),
 ('could_be','It could be one of these.');

INSERT INTO diseases VALUES
 -- bean (unchanged)
 ('bean_angular_leaf_spot','bean','Angular leaf spot','Pseudocercospora griseola','fungal',2,1,0,
  'Fungus favoured by warm wet weather; spreads through seed and old crop.',
  'Grey to brown spots with straight edges boxed in by veins.','draft','CIAT fact sheet (to cite)'),
 ('bean_rust','bean','Bean rust','Uromyces appendiculatus','fungal',2,1,0,
  'Fungus spread by wind; can make leaves fall early.',
  'Small raised reddish brown powdery spots with a yellow ring.','draft','CIAT fact sheet (to cite)'),
 ('bean_healthy','bean','Healthy bean',NULL,'none',0,0,0,
  'No disease found on this leaf.','Even green colour, no spots or powder.','draft','n/a'),
 -- coffee
 ('coffee_leaf_rust','coffee','Coffee leaf rust','Hemileia vastatrix','fungal',2,1,0,
  'Fungus spread by wind and rain splash. Can make the tree drop most of its leaves and lose next season''s harvest.',
  'Orange yellow powdery spots on the underside of leaves, pale yellow spots on top.','draft','Extension fact sheet (to cite)'),
 ('coffee_phoma','coffee','Phoma leaf blight','Phoma spp.','fungal',2,1,0,
  'Fungus favoured by cold, wet and windy weather, common at high altitude. Kills young leaves and shoot tips.',
  'Dark brown to black patches on young leaves, often starting at the edge or tip.','draft','Extension fact sheet (to cite)'),
 ('coffee_healthy','coffee','Healthy coffee',NULL,'none',0,0,0,
  'No disease found on this leaf.','Even dark green colour, no spots or powder.','draft','n/a'),
 ('coffee_cercospora','coffee','Cercospora leaf spot (brown eye spot)','Cercospora coffeicola','fungal',1,1,0,
  'Fungus that is worst on stressed or poorly fed trees and in strong sun. Can also spot the berries.',
  'Round brown spots with a light grey centre and a yellow ring, like an eye.','draft','UH CTAHR PD-41 (to adapt)'),
 ('coffee_leaf_miner','coffee','Coffee leaf miner','Leucoptera spp.','pest',1,1,0,
  'Small moth larvae that tunnel inside the leaf. Heavy attack makes leaves fall early.',
  'Brown irregular dry patches where the leaf surface peels like thin paper.','draft','Regional reference pending'),
 -- maize
 ('maize_common_rust','maize','Common rust','Puccinia sorghi','fungal',1,1,0,
  'Fungus spread by wind. Usually not serious on strong plants, but can reduce yield when many leaves are covered.',
  'Small cinnamon brown powdery bumps on both sides of the leaf.','draft','CIMMYT fact sheet (to cite)'),
 ('maize_gray_leaf_spot','maize','Gray leaf spot','Cercospora zeae-maydis','fungal',2,1,0,
  'Fungus that survives in old maize leaves left in the field. Worst in warm, humid weather.',
  'Long narrow grey or tan spots with straight sides, running between the leaf veins.','draft','CIMMYT fact sheet (to cite)'),
 ('maize_northern_leaf_blight','maize','Northern leaf blight','Exserohilum turcicum','fungal',2,1,0,
  'Fungus favoured by cool, wet weather. Can dry out the leaves early and reduce yield.',
  'Long cigar shaped grey green or tan spots, often longer than a finger.','draft','CIMMYT fact sheet (to cite)'),
 ('maize_healthy','maize','Healthy maize',NULL,'none',0,0,0,
  'No disease found on this leaf.','Even green colour, no spots, streaks or powder.','draft','n/a'),
 -- global
 ('unknown',NULL,'Not recognised',NULL,'unknown',0,0,1,
  'The app could not recognise this photo.','The photo did not match any leaf the app knows.','draft','n/a');

INSERT INTO symptoms (symptom_id, disease_id, sort_order, plant_part, text_en) VALUES
 (1,'bean_angular_leaf_spot',1,'leaf','Grey brown spots boxed in by veins'),
 (2,'bean_angular_leaf_spot',2,'pod','Reddish brown spots on pods'),
 (3,'bean_rust',1,'leaf','Reddish brown powdery spots under the leaf'),
 (4,'bean_rust',2,'leaf','Leaves turn yellow and fall early'),
 (5,'coffee_leaf_rust',1,'leaf','Orange yellow powder on the underside of leaves'),
 (6,'coffee_leaf_rust',2,'leaf','Pale yellow spots on the top of the leaf'),
 (7,'coffee_leaf_rust',3,'whole_plant','Many leaves falling early'),
 (8,'coffee_phoma',1,'leaf','Dark brown to black patches on young leaves'),
 (9,'coffee_phoma',2,'leaf','Young leaves curled or torn'),
 (10,'coffee_phoma',3,'stem','Shoot tips turning black and dying back'),
 (11,'maize_common_rust',1,'leaf','Small oval brown powdery bumps on both sides of the leaf'),
 (12,'maize_common_rust',2,'leaf','Brown powder rubs off on your finger'),
 (13,'maize_gray_leaf_spot',1,'leaf','Grey to tan rectangular spots with straight edges'),
 (14,'maize_gray_leaf_spot',2,'leaf','Spots run along the leaf between the veins'),
 (15,'maize_gray_leaf_spot',3,'whole_plant','Lower leaves die first, then upper leaves'),
 (16,'maize_northern_leaf_blight',1,'leaf','Long cigar shaped grey green to tan spots'),
 (17,'maize_northern_leaf_blight',2,'leaf','Spots join together and leaves dry out'),
 (18,'coffee_cercospora',1,'leaf','Round brown spots with a pale grey centre'),
 (19,'coffee_cercospora',2,'leaf','Yellow ring around each spot'),
 (20,'coffee_cercospora',3,'fruit','Dark sunken patches on berries'),
 (21,'coffee_leaf_miner',1,'leaf','Brown dry blotches on the top of the leaf'),
 (22,'coffee_leaf_miner',2,'leaf','Thin leaf skin over the blotch peels off; tiny larvae may be inside'),
 (23,'coffee_leaf_miner',3,'whole_plant','Many leaves falling in the dry season');

INSERT INTO advice (advice_id, disease_id, kind, method, sort_order, text_en, safety_note) VALUES
 -- bean (unchanged text)
 (1,'bean_angular_leaf_spot','today','cultural',1,'Remove badly spotted leaves. Do not touch plants when they are wet.',NULL),
 (2,'bean_angular_leaf_spot','treatment','chemical',1,'If it keeps spreading, use a fungicide approved for beans in your area.',
  'Follow the label for dose and days before harvest. Wear gloves and a mask. Ask an agriculture officer which product is approved.'),
 (3,'bean_angular_leaf_spot','prevention','cultural',1,'Use clean seed and do not plant beans in the same place next season.',NULL),
 (4,'bean_rust','today','cultural',1,'Remove leaves with the most rust spots.',NULL),
 (5,'bean_rust','treatment','chemical',1,'Use a fungicide approved for bean rust when spots first appear.',
  'Follow the label for dose and days before harvest. Wear gloves and a mask.'),
 (6,'bean_rust','prevention','varietal',1,'Plant rust resistant varieties next season.',NULL),
 -- coffee leaf rust
 (7,'coffee_leaf_rust','today','cultural',1,'Prune crowded branches so air and light reach the leaves. Collect and bury badly infected fallen leaves.',NULL),
 (8,'coffee_leaf_rust','treatment','chemical',1,'Use a fungicide approved for coffee leaf rust in your area. Ask your agriculture officer which one.',
  'Follow the label for dose and days before harvest. Wear gloves and a mask.'),
 (9,'coffee_leaf_rust','prevention','varietal',1,'Ask for rust resistant coffee varieties when you replant.',NULL),
 (10,'coffee_leaf_rust','prevention','cultural',2,'Feed the trees well and keep shade moderate. Weak trees suffer most.',NULL),
 -- coffee phoma
 (11,'coffee_phoma','today','cultural',1,'Cut off dead, blackened shoot tips and burn or bury them.',NULL),
 (12,'coffee_phoma','treatment','chemical',1,'If new shoots keep dying, use a fungicide approved for coffee in your area.',
  'Follow the label for dose and days before harvest. Wear gloves and a mask.'),
 (13,'coffee_phoma','prevention','cultural',1,'Plant windbreak trees or hedges to protect coffee from cold wind.',NULL),
 -- maize common rust
 (14,'maize_common_rust','today','cultural',1,'Count how many leaves are covered. If only a few, keep watching.',NULL),
 (15,'maize_common_rust','treatment','chemical',1,'If most leaves are covered before the tassels appear, ask your agriculture officer whether a fungicide is worth it.',
  'If you spray, follow the label for dose and days before harvest. Wear gloves and a mask.'),
 (16,'maize_common_rust','prevention','varietal',1,'Plant rust resistant maize varieties next season.',NULL),
 -- maize gray leaf spot
 (17,'maize_gray_leaf_spot','today','cultural',1,'Walk the field and check whether the spots are reaching the leaves near the cob.',NULL),
 (18,'maize_gray_leaf_spot','treatment','chemical',1,'If spots reach the leaves near the cob before the grain fills, ask about a fungicide approved for maize.',
  'Follow the label for dose and days before harvest. Wear gloves and a mask.'),
 (19,'maize_gray_leaf_spot','prevention','cultural',1,'Rotate with beans or another crop, and remove or bury old maize stalks after harvest.',NULL),
 -- maize northern leaf blight
 (20,'maize_northern_leaf_blight','today','cultural',1,'Check the leaves near the cob. If they have spots, act this week.',NULL),
 (21,'maize_northern_leaf_blight','treatment','chemical',1,'Ask your agriculture officer about a fungicide approved for maize.',
  'Follow the label for dose and days before harvest. Wear gloves and a mask.'),
 (22,'maize_northern_leaf_blight','prevention','varietal',1,'Plant resistant varieties and rotate with beans.',NULL),
 -- unknown
 (23,'unknown','today','cultural',1,'Take a new photo: one leaf, close up, in daylight, not blurry.',NULL),
 (24,'coffee_cercospora','today','cultural',1,'Check whether the trees look weak or yellow. Feed them and water in dry spells.',NULL),
 (25,'coffee_cercospora','treatment','chemical',1,'If spots spread to berries, ask your agriculture officer about a fungicide approved for coffee.',
  'Follow the label for dose and days before harvest. Wear gloves and a mask.'),
 (26,'coffee_cercospora','prevention','cultural',1,'Keep some shade over the coffee and fertilise regularly. Strong, well fed trees resist it.',NULL),
 (27,'coffee_leaf_miner','today','cultural',1,'Pick and destroy the worst mined leaves. Count how many leaves per branch are affected.',NULL),
 (28,'coffee_leaf_miner','treatment','biological',1,'Wasps and other natural enemies often control the miner. Avoid broad spectrum sprays that kill them.',NULL),
 (29,'coffee_leaf_miner','prevention','cultural',1,'Keep moderate shade and remove weeds. Ask your agriculture officer before using any insecticide.',NULL);

INSERT INTO followup_questions VALUES
 ('q_bean_powder','bean','bean_rust','Does a reddish powder come off on your finger when you rub a spot?',2.0,0.5),
 ('q_coffee_orange_powder','coffee','coffee_leaf_rust','Is there orange or yellow powder under the spots?',2.0,0.5),
 ('q_coffee_tips_dying','coffee','coffee_phoma','Are the youngest shoot tips turning black and dying?',2.0,0.6),
 ('q_coffee_eye_spot','coffee','coffee_cercospora','Do the spots look like an eye: brown with a pale grey centre?',2.0,0.6),
 ('q_coffee_peeling','coffee','coffee_leaf_miner','Does the thin top skin of the brown patch peel off?',2.2,0.5),
 ('q_maize_powder','maize','maize_common_rust','Does brown powder rub off on your finger?',2.0,0.5),
 ('q_maize_cigar','maize','maize_northern_leaf_blight','Are the spots long like a cigar, longer than your finger?',2.0,0.6),
 ('q_maize_rectangles','maize','maize_gray_leaf_spot','Are the spots narrow with straight sides, between the leaf veins?',1.8,0.6);

-- Translations. Hindi is complete for the coffee leaf rust demo path. All 'unverified'.
INSERT INTO translations (entity_type, entity_id, field, lang, text) VALUES
 ('crop','bean','name','hi','सेम (बीन्स)'),
 ('crop','coffee','name','hi','कॉफ़ी'),
 ('crop','maize','name','hi','मक्का'),
 ('crop','bean','name','pa','ਫਲੀਆਂ (ਬੀਨਜ਼)'),
 ('crop','coffee','name','pa','ਕੌਫੀ'),
 ('crop','maize','name','pa','ਮੱਕੀ'),
 ('urgency','0','label','hi','कोई खतरा नहीं'),
 ('urgency','1','label','hi','कम'),
 ('urgency','2','label','hi','मध्यम'),
 ('urgency','3','label','hi','गंभीर'),
 ('urgency','2','action','hi','इसी हफ़्ते कदम उठाएँ'),
 ('urgency','3','action','hi','आज ही कदम उठाएँ'),
 ('disease','coffee_leaf_rust','name','hi','कॉफ़ी का रतुआ'),
 ('disease','coffee_leaf_rust','name','pa','ਕੌਫੀ ਦੀ ਕੁੰਗੀ'),
 ('disease','coffee_cercospora','name','hi','सर्कोस्पोरा पत्ती धब्बा'),
 ('disease','coffee_leaf_miner','name','hi','पत्ती सुरंगक कीट'),
 ('disease','coffee_phoma','name','hi','फोमा'),
 ('disease','coffee_healthy','name','hi','स्वस्थ'),
 ('disease','bean_angular_leaf_spot','name','hi','कोणीय पत्ती धब्बा'),
 ('disease','bean_rust','name','hi','सेम का रतुआ'),
 ('disease','bean_healthy','name','hi','स्वस्थ'),
 ('disease','maize_common_rust','name','hi','मक्का का रतुआ'),
 ('disease','maize_gray_leaf_spot','name','hi','धूसर पत्ती धब्बा'),
 ('disease','maize_northern_leaf_blight','name','hi','उत्तरी पत्ती झुलसा'),
 ('disease','maize_healthy','name','hi','स्वस्थ'),
 ('disease','unknown','name','hi','पहचान नहीं हुई'),
 ('disease','coffee_leaf_rust','summary','hi','यह फफूंद हवा और बारिश की छींटों से फैलती है। यह पेड़ की ज़्यादातर पत्तियाँ गिरा सकती है और अगले मौसम की फ़सल घटा सकती है।'),
 ('disease','coffee_leaf_rust','why','hi','पत्तियों के नीचे नारंगी पीला चूर्ण, और ऊपर हल्के पीले धब्बे।'),
 ('symptom','5','text','hi','पत्तियों के नीचे नारंगी पीला चूर्ण'),
 ('symptom','6','text','hi','पत्ती के ऊपर हल्के पीले धब्बे'),
 ('symptom','7','text','hi','बहुत सी पत्तियाँ जल्दी गिरना'),
 ('advice','7','text','hi','घनी शाखाओं की छँटाई करें ताकि पत्तियों तक हवा और रोशनी पहुँचे। बहुत बीमार गिरी हुई पत्तियाँ इकट्ठा करके गाड़ दें।'),
 ('advice','8','text','hi','अपने क्षेत्र में कॉफ़ी रतुआ के लिए स्वीकृत फफूंदनाशक का प्रयोग करें। अपने कृषि अधिकारी से पूछें कि कौन सा।'),
 ('advice','8','safety_note','hi','मात्रा और कटाई से पहले के दिनों के लिए लेबल का पालन करें। दस्ताने और मास्क पहनें।'),
 ('advice','9','text','hi','दोबारा पौधे लगाते समय रतुआ प्रतिरोधी कॉफ़ी किस्में माँगें।'),
 ('advice','10','text','hi','पेड़ों को अच्छा पोषण दें और छाया संतुलित रखें। कमज़ोर पेड़ ज़्यादा प्रभावित होते हैं।'),
 ('question','q_coffee_orange_powder','text','hi','क्या धब्बों के नीचे नारंगी या पीला चूर्ण है?'),
 ('ui','disclaimer','text','hi','यह कंप्यूटर का अनुमान है, जाँच नहीं। कुछ भी छिड़कने से पहले अपने कृषि अधिकारी या कृषि विक्रेता से पूछें।'),
 ('ui','retake','text','hi','हम यह फ़ोटो नहीं पढ़ पाए। एक पत्ती की, पास से, दिन की रोशनी में नई फ़ोटो लें।'),
 ('ui','uncertain','text','hi','हमें पक्का नहीं पता। कृपया एक या दो सवालों के जवाब दें।'),
 ('ui','could_be','text','hi','यह इनमें से कोई एक हो सकता है।'),
 ('ui','see_expert','text','hi','जल्द ही यह पत्ती किसी कृषि अधिकारी को दिखाएँ।'),
 ('advice','1','text','hi','बहुत धब्बेदार पत्तियाँ हटा दें। गीले पौधों को न छुएँ।'),
 ('advice','2','text','hi','अगर यह फैलता रहे, तो अपने क्षेत्र में सेम के लिए स्वीकृत फफूंदनाशक का प्रयोग करें।'),
 ('advice','3','text','hi','साफ़ बीज इस्तेमाल करें और अगले मौसम में उसी जगह सेम न लगाएँ।'),
 ('advice','4','text','hi','सबसे ज़्यादा रतुआ धब्बों वाली पत्तियाँ हटा दें।'),
 ('advice','5','text','hi','धब्बे दिखते ही सेम के रतुआ के लिए स्वीकृत फफूंदनाशक का प्रयोग करें।'),
 ('advice','6','text','hi','अगले मौसम में रतुआ प्रतिरोधी किस्में लगाएँ।'),
 ('advice','11','text','hi','मरी हुई, काली पड़ी टहनियों के सिरे काटकर जला दें या गाड़ दें।'),
 ('advice','12','text','hi','अगर नई टहनियाँ मरती रहें, तो अपने क्षेत्र में कॉफ़ी के लिए स्वीकृत फफूंदनाशक का प्रयोग करें।'),
 ('advice','13','text','hi','कॉफ़ी को ठंडी हवा से बचाने के लिए हवा रोकने वाले पेड़ या बाड़ लगाएँ।'),
 ('advice','14','text','hi','गिनें कि कितनी पत्तियाँ ढकी हैं। अगर कुछ ही हैं, तो नज़र रखते रहें।'),
 ('advice','15','text','hi','अगर झंडा (नर फूल) निकलने से पहले ज़्यादातर पत्तियाँ ढक जाएँ, तो कृषि अधिकारी से पूछें कि क्या फफूंदनाशक लगाना ठीक रहेगा।'),
 ('advice','16','text','hi','अगले मौसम में रतुआ प्रतिरोधी मक्का किस्में लगाएँ।'),
 ('advice','17','text','hi','खेत में घूमकर देखें कि क्या धब्बे भुट्टे के पास की पत्तियों तक पहुँच रहे हैं।'),
 ('advice','18','text','hi','अगर दाना भरने से पहले धब्बे भुट्टे के पास की पत्तियों तक पहुँच जाएँ, तो मक्का के लिए स्वीकृत फफूंदनाशक के बारे में पूछें।'),
 ('advice','19','text','hi','सेम या किसी दूसरी फ़सल के साथ फ़सल चक्र अपनाएँ, और कटाई के बाद पुराने मक्का के डंठल हटा दें या गाड़ दें।'),
 ('advice','20','text','hi','भुट्टे के पास की पत्तियाँ जाँचें। अगर उन पर धब्बे हैं, तो इसी हफ़्ते कदम उठाएँ।'),
 ('advice','21','text','hi','मक्का के लिए स्वीकृत फफूंदनाशक के बारे में अपने कृषि अधिकारी से पूछें।'),
 ('advice','22','text','hi','प्रतिरोधी किस्में लगाएँ और सेम के साथ फ़सल चक्र अपनाएँ।'),
 ('advice','23','text','hi','नई फ़ोटो लें: एक पत्ती, पास से, दिन की रोशनी में, धुंधली नहीं।'),
 ('advice','24','text','hi','देखें कि क्या पेड़ कमज़ोर या पीले दिखते हैं। उन्हें खाद दें और सूखे में पानी दें।'),
 ('advice','25','text','hi','अगर धब्बे फलों तक फैल जाएँ, तो कॉफ़ी के लिए स्वीकृत फफूंदनाशक के बारे में अपने कृषि अधिकारी से पूछें।'),
 ('advice','26','text','hi','कॉफ़ी पर कुछ छाया रखें और नियमित खाद दें। मज़बूत, अच्छे पोषण वाले पेड़ इसका सामना कर लेते हैं।'),
 ('advice','27','text','hi','सबसे ज़्यादा प्रभावित पत्तियाँ तोड़कर नष्ट करें। गिनें कि हर शाखा पर कितनी पत्तियाँ प्रभावित हैं।'),
 ('advice','28','text','hi','ततैया और दूसरे प्राकृतिक दुश्मन अक्सर इस कीट को काबू में रखते हैं। ऐसे व्यापक कीटनाशक न छिड़कें जो उन्हें मार दें।'),
 ('advice','29','text','hi','मध्यम छाया रखें और खरपतवार हटाएँ। कोई भी कीटनाशक इस्तेमाल करने से पहले कृषि अधिकारी से पूछें।'),
 ('advice','2','safety_note','hi','मात्रा और कटाई से पहले के दिनों के लिए लेबल का पालन करें। दस्ताने और मास्क पहनें। कौन सा उत्पाद स्वीकृत है, यह कृषि अधिकारी से पूछें।'),
 ('advice','5','safety_note','hi','मात्रा और कटाई से पहले के दिनों के लिए लेबल का पालन करें। दस्ताने और मास्क पहनें।'),
 ('advice','12','safety_note','hi','मात्रा और कटाई से पहले के दिनों के लिए लेबल का पालन करें। दस्ताने और मास्क पहनें।'),
 ('advice','15','safety_note','hi','अगर छिड़काव करें, तो मात्रा और कटाई से पहले के दिनों के लिए लेबल का पालन करें। दस्ताने और मास्क पहनें।'),
 ('advice','18','safety_note','hi','मात्रा और कटाई से पहले के दिनों के लिए लेबल का पालन करें। दस्ताने और मास्क पहनें।'),
 ('advice','21','safety_note','hi','मात्रा और कटाई से पहले के दिनों के लिए लेबल का पालन करें। दस्ताने और मास्क पहनें।'),
 ('advice','25','safety_note','hi','मात्रा और कटाई से पहले के दिनों के लिए लेबल का पालन करें। दस्ताने और मास्क पहनें।'),
 ('question','q_bean_powder','text','hi','क्या किसी धब्बे को रगड़ने पर उंगली पर लाल भूरा चूर्ण लगता है?'),
 ('question','q_coffee_tips_dying','text','hi','क्या सबसे नई टहनियों के सिरे काले होकर मर रहे हैं?'),
 ('question','q_coffee_eye_spot','text','hi','क्या धब्बे आँख जैसे दिखते हैं: भूरे, बीच में हल्का धूसर?'),
 ('question','q_coffee_peeling','text','hi','क्या भूरे धब्बे की ऊपरी पतली परत छिल जाती है?'),
 ('question','q_maize_powder','text','hi','क्या रगड़ने पर उंगली पर भूरा चूर्ण लगता है?'),
 ('question','q_maize_cigar','text','hi','क्या धब्बे सिगार जैसे लंबे हैं, आपकी उंगली से भी लंबे?'),
 ('question','q_maize_rectangles','text','hi','क्या धब्बे पतले और सीधे किनारों वाले हैं, पत्ती की नसों के बीच?'),
 ('disease','bean_angular_leaf_spot','why','hi','नसों से घिरे, सीधे किनारों वाले धूसर से भूरे धब्बे।'),
 ('disease','bean_rust','why','hi','पीले घेरे वाले छोटे, उभरे, लाल भूरे चूर्ण वाले धब्बे।'),
 ('disease','bean_healthy','why','hi','एक समान हरा रंग, कोई धब्बा या चूर्ण नहीं।'),
 ('disease','coffee_phoma','why','hi','नई पत्तियों पर गहरे भूरे से काले धब्बे, अक्सर किनारे या सिरे से शुरू।'),
 ('disease','coffee_healthy','why','hi','एक समान गहरा हरा रंग, कोई धब्बा या चूर्ण नहीं।'),
 ('disease','coffee_cercospora','why','hi','हल्के धूसर बीच और पीले घेरे वाले गोल भूरे धब्बे, आँख जैसे।'),
 ('disease','coffee_leaf_miner','why','hi','भूरे अनियमित सूखे धब्बे, जहाँ पत्ती की सतह पतले कागज़ की तरह छिलती है।'),
 ('disease','maize_common_rust','why','hi','पत्ती के दोनों ओर छोटे, दालचीनी जैसे भूरे चूर्ण वाले उभार।'),
 ('disease','maize_gray_leaf_spot','why','hi','पत्ती की नसों के बीच लंबे, पतले, सीधे किनारों वाले धूसर या भूरे धब्बे।'),
 ('disease','maize_northern_leaf_blight','why','hi','लंबे, सिगार जैसे धूसर हरे या भूरे धब्बे, अक्सर उंगली से भी लंबे।'),
 ('disease','maize_healthy','why','hi','एक समान हरा रंग, कोई धब्बा, धारी या चूर्ण नहीं।'),
 ('disease','unknown','why','hi','फ़ोटो ऐप की जानी हुई किसी पत्ती से मेल नहीं खाई।'),
 ('symptom','1','text','hi','नसों से घिरे धूसर भूरे धब्बे'),
 ('symptom','2','text','hi','फलियों पर लाल भूरे धब्बे'),
 ('symptom','3','text','hi','पत्ती के नीचे लाल भूरे चूर्ण वाले धब्बे'),
 ('symptom','4','text','hi','पत्तियाँ पीली होकर जल्दी गिर जाती हैं'),
 ('symptom','8','text','hi','नई पत्तियों पर गहरे भूरे से काले धब्बे'),
 ('symptom','9','text','hi','नई पत्तियाँ मुड़ी या फटी हुई'),
 ('symptom','10','text','hi','टहनियों के सिरे काले होकर सूखते हुए'),
 ('symptom','11','text','hi','पत्ती के दोनों ओर छोटे अंडाकार भूरे चूर्ण वाले उभार'),
 ('symptom','12','text','hi','भूरा चूर्ण उंगली पर लग जाता है'),
 ('symptom','13','text','hi','सीधे किनारों वाले धूसर से भूरे आयताकार धब्बे'),
 ('symptom','14','text','hi','धब्बे नसों के बीच पत्ती की लंबाई में चलते हैं'),
 ('symptom','15','text','hi','पहले नीचे की पत्तियाँ मरती हैं, फिर ऊपर की'),
 ('symptom','16','text','hi','लंबे, सिगार जैसे धूसर हरे से भूरे धब्बे'),
 ('symptom','17','text','hi','धब्बे आपस में मिल जाते हैं और पत्तियाँ सूख जाती हैं'),
 ('symptom','18','text','hi','हल्के धूसर बीच वाले गोल भूरे धब्बे'),
 ('symptom','19','text','hi','हर धब्बे के चारों ओर पीला घेरा'),
 ('symptom','20','text','hi','फलों पर गहरे, धँसे हुए धब्बे'),
 ('symptom','21','text','hi','पत्ती के ऊपर भूरे सूखे धब्बे'),
 ('symptom','22','text','hi','धब्बे की पतली ऊपरी परत छिल जाती है; अंदर छोटे लार्वा हो सकते हैं'),
 ('symptom','23','text','hi','सूखे मौसम में बहुत सी पत्तियाँ गिरना'),
 ('urgency','0','action','hi','कोई कदम ज़रूरी नहीं'),
 ('urgency','1','action','hi','नज़र रखें, 2 हफ़्तों में कदम उठाएँ');

-- Raw dataset labels -> canonical IDs. train.py reads THIS table; it has no mapping of its own.
-- Both PlantVillage spellings are listed ('Corn___' in TFDS, 'Corn_(maize)___' in the GitHub release).
INSERT INTO label_aliases (source, raw_label, disease_id) VALUES
 -- iBean (TFDS 'beans')
 ('ibean','angular_leaf_spot','bean_angular_leaf_spot'),
 ('ibean','bean_rust','bean_rust'),
 ('ibean','healthy','bean_healthy'),
 -- PlantVillage maize (TFDS 'plant_village')
 ('plantvillage','Corn___Cercospora_leaf_spot Gray_leaf_spot','maize_gray_leaf_spot'),
 ('plantvillage','Corn_(maize)___Cercospora_leaf_spot Gray_leaf_spot','maize_gray_leaf_spot'),
 ('plantvillage','Corn___Common_rust','maize_common_rust'),
 ('plantvillage','Corn_(maize)___Common_rust_','maize_common_rust'),
 ('plantvillage','Corn___Northern_Leaf_Blight','maize_northern_leaf_blight'),
 ('plantvillage','Corn_(maize)___Northern_Leaf_Blight','maize_northern_leaf_blight'),
 ('plantvillage','Corn___healthy','maize_healthy'),
 ('plantvillage','Corn_(maize)___healthy','maize_healthy'),
 -- PlantVillage crops we do NOT support -> unknown ("a leaf, but not ours")
 ('plantvillage','Tomato___healthy','unknown'),
 ('plantvillage','Tomato___Early_blight','unknown'),
 ('plantvillage','Tomato___Late_blight','unknown'),
 ('plantvillage','Potato___healthy','unknown'),
 ('plantvillage','Apple___healthy','unknown'),
 ('plantvillage','Grape___healthy','unknown'),
 ('plantvillage','Peach___healthy','unknown'),
 ('plantvillage','Squash___Powdery_mildew','unknown'),
 ('plantvillage','Pepper,_bell___healthy','unknown'),
 ('plantvillage','Orange___Haunglongbing_(Citrus_greening)','unknown'),
 ('plantvillage','Background_without_leaves','unknown'),
 -- Deliberately dropped: soybean is a legume that looks too much like bean
 ('plantvillage','Soybean___healthy',NULL),
 -- Coffee, Soroti University Uganda dataset (Mendeley k36wnd6knb). Folder names not yet
 -- confirmed: these are the plausible spellings. prep stops and prints any unmapped folder.
 ('coffee_uganda','Healthy','coffee_healthy'),
 ('coffee_uganda','Coffee Healthy','coffee_healthy'),
 ('coffee_uganda','Coffee Leaf Rust','coffee_leaf_rust'),
 ('coffee_uganda','Leaf Rust','coffee_leaf_rust'),
 ('coffee_uganda','Rust','coffee_leaf_rust'),
 ('coffee_uganda','Coffee Rust','coffee_leaf_rust'),
 ('coffee_uganda','Phoma','coffee_phoma'),
 ('coffee_uganda','Phoma Disease','coffee_phoma'),
 ('coffee_uganda','Coffee Phoma','coffee_phoma'),
 ('jmuben','Cerscospora','coffee_cercospora'),
 ('jmuben','Cercospora','coffee_cercospora'),
 ('jmuben','Leaf rust','coffee_leaf_rust'),
 ('jmuben','Leaf_rust','coffee_leaf_rust'),
 ('jmuben','Rust','coffee_leaf_rust'),
 ('jmuben','Phoma','coffee_phoma'),
 ('jmuben','Forma','coffee_phoma'),
 ('jmuben','Healthy','coffee_healthy'),
 ('jmuben','Miner','coffee_leaf_miner'),
 ('jmuben','Leaf miner','coffee_leaf_miner');

-- v0.2  = crop set C, 11 classes (primary)
-- v0.2b = bean + maize fallback, 8 classes
-- Both alphabetical, so index order == Keras folder order.
INSERT INTO model_classes (model_version, class_index, disease_id) VALUES
 ('v0.2',0,'bean_angular_leaf_spot'),
 ('v0.2',1,'bean_healthy'),
 ('v0.2',2,'bean_rust'),
 ('v0.2',3,'coffee_healthy'),
 ('v0.2',4,'coffee_leaf_rust'),
 ('v0.2',5,'coffee_phoma'),
 ('v0.2',6,'maize_common_rust'),
 ('v0.2',7,'maize_gray_leaf_spot'),
 ('v0.2',8,'maize_healthy'),
 ('v0.2',9,'maize_northern_leaf_blight'),
 ('v0.2',10,'unknown'),
 ('v0.2b',0,'bean_angular_leaf_spot'),
 ('v0.2b',1,'bean_healthy'),
 ('v0.2b',2,'bean_rust'),
 ('v0.2b',3,'maize_common_rust'),
 ('v0.2b',4,'maize_gray_leaf_spot'),
 ('v0.2b',5,'maize_healthy'),
 ('v0.2b',6,'maize_northern_leaf_blight'),
 ('v0.2b',7,'unknown'),
 ('v0.3',0,'bean_angular_leaf_spot'),
 ('v0.3',1,'bean_healthy'),
 ('v0.3',2,'bean_rust'),
 ('v0.3',3,'coffee_cercospora'),
 ('v0.3',4,'coffee_healthy'),
 ('v0.3',5,'coffee_leaf_miner'),
 ('v0.3',6,'coffee_leaf_rust'),
 ('v0.3',7,'coffee_phoma'),
 ('v0.3',8,'maize_common_rust'),
 ('v0.3',9,'maize_gray_leaf_spot'),
 ('v0.3',10,'maize_healthy'),
 ('v0.3',11,'maize_northern_leaf_blight'),
 ('v0.3',12,'unknown');
