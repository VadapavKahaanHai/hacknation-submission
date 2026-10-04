"""Mapping contracts and bilingual technical fallbacks; never approve agronomic content."""
import json
import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import class_mapping as mapping
import preprocess_coffee as prep
import test_runtime_api as runtime_fixture
from test_runtime_api import new_id


class MappingContracts(unittest.TestCase):
    def test_every_alias_and_dataset(self):
        catalog=json.loads((ROOT/'knowledge/catalog.json').read_text(encoding='utf-8'))
        mapping.validate_classes(catalog['classes'])
        self.assertEqual(mapping.MODEL_CLASS_IDS,(1,2,3,4,5,6))
        self.assertEqual(prep.CLASSES,mapping.CLASS_IDS)
        for alias,label in mapping.ALIASES.items():
            with self.subTest(alias=alias):
                self.assertEqual(mapping.canonical_label('  '+alias.upper()+'  '),label)
                own=prep.classify('own',Path(alias)/'leaf.jpg')
                self.assertEqual(own[:2],('own_photos',label))
                if label!='unknown':
                    dataset='jmuben' if label in ('Rust','Cercospora','Phoma') else 'jmuben2'
                    self.assertEqual(prep.classify('jmuben',Path(alias)/'leaf.jpg')[:2],(dataset,label))
        self.assertIsNone(mapping.canonical_label('unrecognised disease'))
        self.assertEqual(prep.classify('plantdoc',Path('train/Apple rust leaf/a.jpg')),
                         ('plantdoc','unknown','Apple rust leaf','ood_test'))
        self.assertEqual(mapping.CLASS_TYPES[4],'pest')
        self.assertEqual(mapping.DISPLAY_NAMES[3][0],'Phoma disease')
        self.assertEqual(mapping.DISPLAY_NAMES[5],('Healthy leaf','स्वस्थ पत्ती'))

    def test_permuted_model_labels_and_types_rejected(self):
        classes=json.loads((ROOT/'knowledge/catalog.json').read_text(encoding='utf-8'))['classes']
        swapped=[dict(r) for r in classes]
        swapped[0]['label_en'],swapped[1]['label_en']=swapped[1]['label_en'],swapped[0]['label_en']
        with self.assertRaises(ValueError):
            mapping.validate_classes(swapped)
        wrong_type=[dict(r) for r in classes];wrong_type[3]['type']='disease'
        with self.assertRaises(ValueError):
            mapping.validate_classes(wrong_type)
        with self.assertRaises(ValueError):
            mapping.validate_classes(classes[:-1])

    def test_every_fallback_has_both_languages(self):
        required={'unknown_input','low_confidence','close_scores','no_approved_advice',
                  'advice_language_unavailable','unreadable_image','model_unavailable','pending_sync'}
        self.assertTrue(required.issubset(mapping.MESSAGES))
        for code,texts in mapping.MESSAGES.items():
            self.assertEqual(set(texts),{'en','hi'})
            for language in ('en','hi'):
                result=mapping.fallback(code,language)
                self.assertEqual(result['code'],code)
                self.assertEqual(result['language'],language)
                self.assertTrue(result['text'].strip())
                self.assertFalse(result['is_treatment_advice'])
                self.assertFalse(result['language_fallback'])
            self.assertTrue(any('\u0900'<=c<='\u097f' for c in texts['hi']))
        self.assertTrue(mapping.fallback('unsupported_language','ta')['language_fallback'])


class MappingRuntimeTests(unittest.TestCase):
    # Reuse fixture setup/helpers without inheriting or rerunning unrelated test methods.
    setUp=runtime_fixture.RuntimeTests.setUp
    request=runtime_fixture.RuntimeTests.request
    payload=runtime_fixture.RuntimeTests.payload
    scalar=runtime_fixture.RuntimeTests.scalar

    def test_each_model_output_maps_to_its_class_and_advice(self):
        for class_id in mapping.MODEL_CLASS_IDS:
            scores=[0.02]*6;scores[class_id-1]=0.9
            self.runtime.predictor=lambda image,s=scores:s
            result,_=self.runtime.submit_image(new_id(),self.device,self.photo)
            self.assertEqual(result['top_class_id'],class_id)
            self.assertEqual(result['top_class']['display_name_en'],mapping.DISPLAY_NAMES[class_id][0])
            self.assertEqual(result['top_class']['type'],mapping.CLASS_TYPES[class_id])
            if class_id==1:
                self.assertEqual([r['class_id'] for r in result['advice']],[1])
            else:
                self.assertEqual(result['advice'],[])
            if class_id==6:
                self.assertEqual(result['status'],'not_sure_ask_a_person')
                self.assertEqual(result['fallbacks'][0]['code'],'unknown_input')
            elif class_id!=1:
                self.assertEqual(result['fallbacks'][0]['code'],'no_approved_advice')

    def test_display_names_and_language_missing_vs_unreviewed(self):
        status,classes,_=self.request('GET','/classes',query='language=hi')
        self.assertEqual(status,200)
        self.assertEqual(classes[0]['label_en'],'Rust')  # frozen key preserved
        self.assertEqual(classes[0]['display_name'],'कॉफी पत्ती का रतुआ')
        status,result,_=self.request('GET','/advice-status',query='class_id=1&language=hi')
        self.assertEqual(status,200)
        self.assertEqual(result['advice'],[])
        self.assertEqual(result['available_advice_languages'],['en'])
        self.assertEqual(result['fallbacks'][0]['code'],'advice_language_unavailable')
        self.assertEqual(result['fallbacks'][0]['language'],'hi')
        result=self.request('GET','/advice-status',query='class_id=2&language=hi')[1]
        self.assertEqual(result['fallbacks'][0]['code'],'no_approved_advice')
        self.assertNotIn('SECRET DRAFT',json.dumps(result))
        self.assertEqual(self.request('GET','/advice',query='class_id=2')[1],[])  # compatible route
        self.assertEqual(len(self.request('GET','/fallbacks',query='language=hi')[1]),len(mapping.MESSAGES))

    def test_low_margin_fallbacks_and_sync_lifecycle(self):
        self.runtime.predictor=lambda image:[0.4,0.3,0.1,0.1,0.05,0.05]
        low,_=self.runtime.submit_image(new_id(),self.device,self.photo,'hi')
        self.assertEqual(low['fallbacks'][0]['code'],'low_confidence')
        self.assertTrue(all(f['language']=='hi' for f in low['fallbacks']))
        self.assertFalse(low['advice_available'])
        queued=self.runtime.list_pending_sync()[0]
        self.runtime.acknowledge_sync(low['prediction_id'],queued['sync_token'])
        fetched=self.runtime.get_prediction(low['prediction_id'])
        self.assertNotIn('pending_sync',[f['code'] for f in fetched['fallbacks']])
        self.runtime.resolve_escalation(low['prediction_id'])
        self.assertIn('pending_sync',[f['code'] for f in self.runtime.get_prediction(low['prediction_id'])['fallbacks']])
        # A separate unused version permits testing the close-score rule in isolation.
        with self.runtime.connection(write=True) as db:
            db.execute('''INSERT INTO model_versions(model_id,version,base_model,quantization,file_size_mb,
                          confidence_threshold,min_margin) VALUES ('close-test','close-test','TEST','int8',1,0.4,0.1)''')
        self.runtime.model_id='close-test'
        self.runtime.predictor=lambda image:[0.45,0.45,0.025,0.025,0.025,0.025]
        close,_=self.runtime.submit_image(new_id(),self.device,self.photo)
        self.assertEqual(close['fallbacks'][0]['code'],'close_scores')

    def test_unreadable_model_unavailable_and_unsupported_language(self):
        body=self.payload();body['language']='hi';body['image_base64']='aW52YWxpZA=='
        status,result,_=self.request('POST','/predictions',body)
        self.assertEqual(status,400)
        self.assertEqual(result['fallback']['code'],'unreadable_image')
        self.assertEqual(result['fallback']['language'],'hi')
        body['image_base64']='not base64!'
        status,result,_=self.request('POST','/predictions',body)
        self.assertEqual(status,400)
        self.assertEqual(result['fallback']['code'],'unreadable_image')
        self.runtime.predictor=None
        status,result,_=self.request('POST','/predictions',dict(self.payload(),language='hi'))
        self.assertEqual(status,503)
        self.assertEqual(result['fallback']['code'],'model_unavailable')
        self.assertEqual(self.scalar('SELECT count(*) FROM predictions'),0)
        status,result,_=self.request('GET','/classes',query='language=ta')
        self.assertEqual(status,400)
        self.assertEqual(result['fallback']['code'],'unsupported_language')
        self.assertTrue(result['fallback']['language_fallback'])


if __name__=='__main__':
    unittest.main()
