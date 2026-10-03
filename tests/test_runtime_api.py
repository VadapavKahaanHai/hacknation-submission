"""Repository, WSGI and real localhost HTTP tests; temporary databases only."""
import base64
import contextlib
import hashlib
import http.client
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
import uuid
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import make_server
from wsgiref.util import setup_testing_defaults

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from runtime_db import Runtime,RequestError,initialize_runtime,clean_upload
from serve_api import API,QuietHandler,MAX_BODY


def new_id():
    return str(uuid.uuid4())


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='coffee-api-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.catalog=self.root/'catalog.sqlite'
        self.database=self.root/'runtime'/'coffee.sqlite'
        with contextlib.closing(sqlite3.connect(self.catalog)) as db:
            db.executescript((ROOT/'sql/coffee_schema.sql').read_text(encoding='utf-8'))
            catalog=json.loads((ROOT/'knowledge/catalog.json').read_text(encoding='utf-8'))
            db.executemany('INSERT INTO classes(class_id,label_en,label_hi,type) VALUES (?,?,?,?)',
                           [(r['class_id'],r['label_en'],r['label_hi'],r['type']) for r in catalog['classes']])
            db.execute("INSERT INTO database_metadata VALUES ('purpose','development_catalog')")
            db.execute('''INSERT INTO advice(advice_id,class_id,language,advice_text,action_steps,source_citation,
                reviewed_by_human,reviewer,review_date) VALUES
                ('approved',1,'en','TEST APPROVED','TEST STEP','TEST SOURCE',1,'TEST-ONLY','2020-01-01T00:00:00Z')''')
            db.execute('''INSERT INTO advice(advice_id,class_id,language,advice_text,action_steps,source_citation)
                          VALUES ('draft',2,'en','SECRET DRAFT','TEST','TEST')''')
            db.commit()
        self.catalog_hash=hashlib.sha256(self.catalog.read_bytes()).hexdigest()
        initialize_runtime(self.catalog,self.database,mock=True)
        self.runtime=Runtime(self.database)
        self.device=new_id()
        self.token='test-only-token-'+'a'*40
        self.app=API(self.runtime,self.token)
        exif=Image.Exif()
        exif[274]=6
        exif[270]='PRIVATE METADATA'
        buffer=io.BytesIO()
        Image.new('RGB',(30,20),'green').save(buffer,'JPEG',exif=exif)
        self.photo=buffer.getvalue()

    def request(self,method,path,body=None,token=True,query='',**overrides):
        environ={}
        setup_testing_defaults(environ)
        encoded=json.dumps(body).encode() if body is not None else b''
        environ.update(REQUEST_METHOD=method,PATH_INFO=path,QUERY_STRING=query,
                       CONTENT_LENGTH=str(len(encoded)),CONTENT_TYPE='application/json')
        environ['wsgi.input']=io.BytesIO(encoded)
        if token:
            environ['HTTP_AUTHORIZATION']='Bearer '+self.token
        environ.update(overrides)
        status=[]
        result=b''.join(self.app(environ,lambda s,h:status.append((int(s.split()[0]),dict(h)))))
        return status[0][0],json.loads(result),status[0][1]

    def payload(self,prediction_id=None):
        return dict(prediction_id=prediction_id or new_id(),device_id=self.device,
                    image_base64=base64.b64encode(self.photo).decode())

    def scalar(self,sql):
        with self.runtime.connection() as db:
            return db.execute(sql).fetchone()[0]

    def test_isolation_metadata_and_idempotence(self):
        payload=self.payload()
        status,result,_=self.request('POST','/predictions',payload)
        self.assertEqual(status,201)
        self.assertTrue(result['is_mock'])
        self.assertEqual(result['status'],'not_sure_ask_a_person')
        self.assertEqual(result['escalation']['reason'],'unknown_input')
        self.assertEqual(result['advice'],[])
        path=self.database.parent/result['image_path']
        with Image.open(path) as image:
            self.assertEqual(image.size,(20,30))
            self.assertEqual(image.info,{})
            self.assertFalse(image.getexif())
        self.assertNotIn(b'PRIVATE METADATA',path.read_bytes())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),result['media']['sha256'])
        self.assertEqual(self.request('POST','/predictions',payload)[0],200)
        self.assertEqual(self.scalar('SELECT count(*) FROM predictions'),1)
        self.assertEqual(self.scalar('SELECT count(*) FROM sync_queue'),1)
        self.assertEqual(self.scalar('SELECT count(*) FROM images'),0)
        self.assertEqual(self.scalar('SELECT count(*) FROM advice'),1)
        payload['device_id']=new_id()
        self.assertEqual(self.request('POST','/predictions',payload)[0],409)
        self.assertEqual(hashlib.sha256(self.catalog.read_bytes()).hexdigest(),self.catalog_hash)
        with self.assertRaises(ValueError):
            Runtime(self.catalog)
        with self.assertRaises(ValueError):
            initialize_runtime(self.catalog,self.database,mock=True)

    def test_scores_advice_and_revocation(self):
        self.runtime.predictor=lambda image:[0.8,0.1,0.025,0.025,0.025,0.025]
        result,_=self.runtime.submit_image(new_id(),self.device,self.photo)
        self.assertEqual(result['status'],'confident')
        self.assertTrue(result['advice_available'])
        self.assertIsNone(result['escalation'])
        self.assertEqual(self.runtime.get_reviewed_advice(2),[])
        self.assertEqual(self.runtime.get_prediction(result['prediction_id'],'hi')['advice'],[])
        with self.runtime.connection(write=True) as db:
            db.execute("UPDATE advice SET prevention='changed' WHERE advice_id='approved'")
        self.assertEqual(self.runtime.get_prediction(result['prediction_id'])['advice'],[])

    def test_close_and_low_confidence(self):
        with self.runtime.connection(write=True) as db:
            db.execute("UPDATE model_versions SET confidence_threshold=0.4 WHERE model_id='mock-v1'")
        self.runtime.predictor=lambda image:[0.45,0.45,0.025,0.025,0.025,0.025]
        close,_=self.runtime.submit_image(new_id(),self.device,self.photo)
        self.assertEqual(close['escalation']['reason'],'close_scores')
        self.runtime.predictor=lambda image:[0.3,0.25,0.2,0.1,0.1,0.05]
        low,_=self.runtime.submit_image(new_id(),self.device,self.photo)
        self.assertEqual(low['escalation']['reason'],'low_confidence')
        self.assertEqual(len(self.runtime.list_predictions(self.device,limit=1)),1)

    def test_stale_ack_failure_and_requeue(self):
        result,_=self.runtime.submit_image(new_id(),self.device,self.photo)
        pid=result['prediction_id']
        old=self.runtime.list_pending_sync()[0]
        self.runtime.acknowledge_sync(pid,old['sync_token'],'timeout')
        self.assertEqual(self.runtime.list_pending_sync()[0]['attempts'],1)
        self.runtime.resolve_escalation(pid)
        with self.assertRaises(RequestError) as error:
            self.runtime.acknowledge_sync(pid,old['sync_token'])
        self.assertEqual(error.exception.status,409)
        current=self.runtime.list_pending_sync()[0]
        self.runtime.acknowledge_sync(pid,current['sync_token'])
        self.runtime.acknowledge_sync(pid,current['sync_token'])
        self.assertEqual(self.runtime.list_pending_sync(),[])
        self.assertEqual(self.runtime.get_prediction(pid)['synced'],1)
        self.runtime.resolve_escalation(pid,'extension_service')
        self.assertEqual(self.runtime.get_prediction(pid)['synced'],0)
        self.assertEqual(len(self.runtime.list_pending_sync()),1)

    def test_invalid_models_and_write_rollback(self):
        for scores in ([float('nan')]*6,[1,1,0,0,0,0],[1,0],[-1,1,1,0,0,0],[True,0,0,0,0,0]):
            self.runtime.predictor=lambda image,s=scores:s
            with self.assertRaises(RequestError):
                self.runtime.submit_image(new_id(),self.device,self.photo)
        self.assertEqual(self.scalar('SELECT count(*) FROM predictions'),0)
        self.runtime.predictor=lambda image:[0.1,0.1,0.1,0.1,0.1,0.5]
        self.runtime.model_id='missing-model'
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.submit_image(new_id(),self.device,self.photo)
        self.assertEqual(self.scalar('SELECT count(*) FROM sync_queue'),0)
        self.assertEqual(list(self.runtime.media.glob('*.png')),[])

    def test_input_and_auth_boundaries(self):
        self.assertEqual(self.request('GET','/health',token=False)[0],200)
        self.assertEqual(self.request('GET','/classes',token=False)[0],401)
        self.assertEqual(self.request('GET','/classes')[0],200)
        self.assertEqual(self.request('GET','/advice',query='class_id=2')[1],[])
        self.assertEqual(self.request('GET','/sync/pending',query='limit=101')[0],400)
        self.assertEqual(self.request('GET','/sync/pending',query='limit=1&limit=2')[0],400)
        self.assertEqual(self.request('DELETE','/predictions')[0],405)
        self.assertEqual(self.request('GET','/missing')[0],404)
        self.assertEqual(self.request('GET','/predictions/'+new_id())[0],404)
        body=self.payload()
        body['farmer_name']='not allowed'
        self.assertEqual(self.request('POST','/predictions',body)[0],400)
        self.assertEqual(self.request('POST','/predictions',self.payload(),CONTENT_LENGTH=str(MAX_BODY+1))[0],413)
        self.assertEqual(self.request('POST','/predictions',self.payload(),CONTENT_TYPE='text/plain')[0],415)
        body=self.payload();body['image_base64']='not base64!'
        self.assertEqual(self.request('POST','/predictions',body)[0],400)
        body=self.payload();body['image_base64']=base64.b64encode(b'not an image').decode()
        self.assertEqual(self.request('POST','/predictions',body)[0],400)
        body=self.payload();body['prediction_id']='../../escape'
        self.assertEqual(self.request('POST','/predictions',body)[0],400)
        body=self.payload();body['language']='xx'
        self.assertEqual(self.request('POST','/predictions',body)[0],400)
        with patch('runtime_db.MAX_PIXELS',10):
            with self.assertRaises(RequestError) as error:
                clean_upload(self.photo)
            self.assertEqual(error.exception.status,413)

    def test_no_model_and_real_http(self):
        blank=self.root/'blank'/'coffee.sqlite'
        initialize_runtime(self.catalog,blank,mock=False)
        runtime=Runtime(blank)
        with self.assertRaises(RequestError) as error:
            runtime.submit_image(new_id(),self.device,self.photo)
        self.assertEqual(error.exception.code,'model_not_configured')
        server=make_server('127.0.0.1',0,self.app,handler_class=QuietHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                connection.request('GET','/health')
                response=connection.getresponse()
                self.assertEqual(response.status,200)
                self.assertTrue(json.loads(response.read())['is_mock'])
                connection.request('POST','/predictions',json.dumps(self.payload()),
                                   {'Content-Type':'application/json','Authorization':'Bearer '+self.token})
                response=connection.getresponse()
                self.assertEqual(response.status,201)
                self.assertEqual(json.loads(response.read())['status'],'not_sure_ask_a_person')
            finally:
                connection.close()
        finally:
            server.shutdown();thread.join(timeout=5);server.server_close()

    def test_parallel_retry_and_http_sync_routes(self):
        pid=new_id()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.runtime.submit_image(pid,self.device,self.photo),range(2)))
        self.assertEqual(sum(created for _,created in results),1)
        self.assertEqual(self.scalar('SELECT count(*) FROM predictions'),1)
        self.assertEqual(self.request('GET','/predictions/'+pid)[0],200)
        self.assertEqual(len(self.request('GET','/predictions',query='device_id='+self.device)[1]),1)
        queued=self.request('GET','/sync/pending')[1][0]
        body=dict(prediction_id=pid,sync_token=queued['sync_token'])
        self.assertEqual(self.request('POST','/sync/failure',dict(body,error_code='timeout'))[0],200)
        self.assertEqual(self.request('POST','/escalations/resolve',dict(prediction_id=pid))[0],200)
        self.assertEqual(self.request('POST','/sync/ack',body)[0],409)
        queued=self.request('GET','/sync/pending')[1][0]
        body['sync_token']=queued['sync_token']
        self.assertEqual(self.request('POST','/sync/ack',body)[1],{'acknowledged':True})
        self.assertEqual(self.request('GET','/sync/pending')[1],[])


if __name__=='__main__':
    unittest.main()
