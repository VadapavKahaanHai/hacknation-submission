"""Runtime query/inference functions; isolated from the training catalog."""
import hashlib
import io
import json
import math
from numbers import Real
import os
import sqlite3
import tempfile
import uuid
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from class_mapping import validate_classes, class_display, MODEL_CLASS_IDS, fallback

ROOT = Path(__file__).resolve().parents[1]
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 12_000_000


class RequestError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def uuid_value(value):
    try:
        parsed = uuid.UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError
        return value
    except (ValueError,TypeError,AttributeError):
        raise RequestError(400,'expected_random_uuid4') from None


def language_value(value):
    if value not in ('en','hi'):
        raise RequestError(400,'unsupported_language')
    return value


def bounded_limit(value):
    if type(value) is not int or not 1 <= value <= 100:
        raise RequestError(400,'limit_must_be_1_to_100')
    return value


def clean_upload(data):
    if not isinstance(data,bytes) or not data or len(data)>MAX_IMAGE_BYTES:
        raise RequestError(413,'image_size_limit')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.width*source.height>MAX_PIXELS:
                    raise RequestError(413,'image_pixel_limit')
                if source.format not in ('JPEG','PNG','WEBP') or getattr(source,'n_frames',1)!=1:
                    raise RequestError(415,'unsupported_image')
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                rgba = oriented.convert('RGBA')
                rgb = Image.new('RGB',rgba.size,'white')
                rgb.paste(rgba,mask=rgba.getchannel('A'))
                clean = Image.frombytes('RGB',rgb.size,rgb.tobytes())
        buffer = io.BytesIO()
        clean.save(buffer,format='PNG')
        return clean,buffer.getvalue()
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):
        raise RequestError(400,'unreadable_image') from None


def initialize_runtime(catalog, destination, mock=False):
    catalog,destination = Path(catalog).resolve(),Path(destination).resolve()
    if destination.exists():
        raise ValueError('Runtime destination exists; initialization never overwrites it')
    source = sqlite3.connect(catalog.as_uri()+'?mode=ro',uri=True)
    source.row_factory = sqlite3.Row
    temporary = None
    db = None
    try:
        if source.execute('PRAGMA user_version').fetchone()[0]!=2:
            raise ValueError('Expected version-2 catalog')
        purpose = source.execute("SELECT value FROM database_metadata WHERE key='purpose'").fetchone()
        if purpose is None or purpose[0]!='development_catalog':
            raise ValueError('Initialization requires a development catalog')
        classes = source.execute('SELECT class_id,label_en,label_hi,type FROM classes ORDER BY class_id').fetchall()
        validate_classes(classes)
        # Select by view: drafts never enter the runtime package.
        advice = source.execute('SELECT * FROM reviewed_advice').fetchall()
        destination.parent.mkdir(parents=True,exist_ok=True)
        fd,name = tempfile.mkstemp(prefix=destination.name+'.',suffix='.tmp',dir=destination.parent)
        os.close(fd)
        temporary = Path(name)
        db = sqlite3.connect(temporary)
        db.executescript((ROOT/'sql'/'coffee_schema.sql').read_text(encoding='utf-8'))
        db.executescript((ROOT/'sql'/'runtime.sql').read_text(encoding='utf-8'))
        with db:
            db.executemany('INSERT INTO classes(class_id,label_en,label_hi,type) VALUES (?,?,?,?)',classes)
            for row in advice:
                values = dict(row,reviewed_by_human=1)
                fields = list(values)
                db.execute('INSERT INTO advice ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',
                           [values[f] for f in fields])
            db.executemany('INSERT INTO database_metadata VALUES (?,?)',[
                ('purpose','runtime'),('inference_mode','mock' if mock else 'unconfigured'),
                ('media_root','media'),('created_at',timestamp())])
            if mock:
                db.execute('''INSERT INTO model_versions(model_id,version,base_model,quantization,
                    file_size_mb,confidence_threshold,min_margin) VALUES
                    ('mock-v1','mock-v1','MOCK_UNKNOWN_ONLY_NO_TRAINED_WEIGHTS','float32',0.000001,0.8,0.1)''')
        db.close();db=None
        if destination.exists():
            raise ValueError('Destination appeared during initialization')
        temporary.rename(destination)
    finally:
        source.close()
        if db is not None:
            db.close()
        if temporary is not None and temporary.exists():
            temporary.unlink()


class Runtime:
    def __init__(self,database,predictor=None,model_id=None):
        self.database=Path(database).resolve()
        with self.connection() as db:
            metadata=dict(db.execute('SELECT key,value FROM database_metadata'))
            if metadata.get('purpose')!='runtime':
                raise ValueError('Refusing to use the development catalog as a runtime database')
            self.mode=metadata['inference_mode']
            validate_classes(db.execute('SELECT class_id,label_en,type FROM classes').fetchall())
        self.media=self.database.parent/'media'
        self.predictor=predictor
        self.model_id=model_id
        if self.mode=='mock' and predictor is None:
            self.predictor=lambda image: [0.1,0.1,0.1,0.1,0.1,0.5]
            self.model_id='mock-v1'
        if self.predictor is not None:
            with self.connection() as db:
                if not db.execute('SELECT 1 FROM model_versions WHERE model_id=?',(self.model_id,)).fetchone():
                    raise ValueError('Model adapter needs a registered model version')

    @contextmanager
    def connection(self,write=False):
        db=sqlite3.connect(self.database.as_uri()+'?mode=rw',uri=True,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def classes(self,language='en'):
        language_value(language)
        with self.connection() as db:
            return [class_display(r,language) for r in db.execute('SELECT class_id,label_en,label_hi,type FROM classes ORDER BY class_id')]

    def advice_status(self,class_id,language='en'):
        language_value(language)
        if type(class_id) is not int or class_id not in MODEL_CLASS_IDS:
            raise RequestError(400,'invalid_class')
        with self.connection() as db:
            advice=[dict(r) for r in db.execute('SELECT * FROM reviewed_advice WHERE class_id=? AND language=?',(class_id,language))]
            languages=[r[0] for r in db.execute('SELECT language FROM reviewed_advice WHERE class_id=? ORDER BY language',(class_id,))]
            return {'class':class_display(db.execute('SELECT * FROM classes WHERE class_id=?',(class_id,)).fetchone(),language),
                    'language':language,'advice':advice,'advice_available':bool(advice),
                    'available_advice_languages':languages,
                    'fallbacks':[] if advice else [fallback('advice_language_unavailable' if languages else 'no_approved_advice',language)]}

    def get_reviewed_advice(self,class_id,language='en'):
        language_value(language)
        if type(class_id) is not int or class_id not in range(1,7):
            raise RequestError(400,'invalid_class')
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM reviewed_advice WHERE class_id=? AND language=?',
                                                (class_id,language))]

    def _prediction(self,db,prediction_id,language):
        prediction=db.execute('SELECT * FROM predictions WHERE prediction_id=?',(prediction_id,)).fetchone()
        if prediction is None:
            raise RequestError(404,'prediction_not_found')
        result=dict(prediction)
        escalation=db.execute('SELECT * FROM escalations WHERE prediction_id=?',(prediction_id,)).fetchone()
        result['escalation']=dict(escalation) if escalation else None
        result['media']=dict(db.execute('SELECT * FROM prediction_media WHERE prediction_id=?',(prediction_id,)).fetchone())
        result['advice']=[dict(r) for r in db.execute('SELECT * FROM prediction_advice WHERE prediction_id=? AND language=?',
                                                     (prediction_id,language))]
        result['advice_available']=bool(result['advice'])
        result['is_mock']=self.mode=='mock'
        result['top_class']=class_display(db.execute('SELECT class_id,label_en,label_hi,type FROM classes WHERE class_id=?',
                                                      (result['top_class_id'],)).fetchone(),language)
        result['second_class']=class_display(db.execute('SELECT class_id,label_en,label_hi,type FROM classes WHERE class_id=?',
                                                         (result['second_class_id'],)).fetchone(),language)
        result['fallbacks']=[]
        if result['status']=='not_sure_ask_a_person':
            reason=db.execute('SELECT reason FROM prediction_decisions WHERE prediction_id=?',(prediction_id,)).fetchone()[0]
            result['fallbacks'].append(fallback(reason,language))
        elif not result['advice']:
            other=db.execute('SELECT 1 FROM reviewed_advice WHERE class_id=? LIMIT 1',(result['top_class_id'],)).fetchone()
            result['fallbacks'].append(fallback('advice_language_unavailable' if other else 'no_approved_advice',language))
        if db.execute('SELECT 1 FROM sync_queue WHERE row_id=?',(prediction_id,)).fetchone():
            result['fallbacks'].append(fallback('pending_sync',language))
        if result['is_mock']:
            result['fallbacks'].append(fallback('mock_result',language))
        return result

    def get_prediction(self,prediction_id,language='en'):
        uuid_value(prediction_id);language_value(language)
        with self.connection() as db:
            return self._prediction(db,prediction_id,language)

    def list_predictions(self,device_id,limit=20,offset=0,language='en'):
        uuid_value(device_id);bounded_limit(limit);language_value(language)
        if type(offset) is not int or not 0<=offset<=1_000_000:
            raise RequestError(400,'invalid_offset')
        with self.connection() as db:
            ids=db.execute('SELECT prediction_id FROM predictions WHERE device_id=? ORDER BY timestamp DESC,prediction_id LIMIT ? OFFSET ?',
                           (device_id,limit,offset)).fetchall()
            return [self._prediction(db,r[0],language) for r in ids]

    def submit_image(self,prediction_id,device_id,data,language='en'):
        uuid_value(prediction_id);uuid_value(device_id);language_value(language)
        image,encoded=clean_upload(data)
        photo_hash=digest(encoded)
        created_path=None
        try:
            # ponytail: serialize single-device inference with writes; use a worker queue
            # if model latency or concurrent request volume makes this a bottleneck.
            with self.connection(write=True) as db:
                old=db.execute('SELECT * FROM predictions WHERE prediction_id=?',(prediction_id,)).fetchone()
                if old:
                    media=db.execute('SELECT sha256 FROM prediction_media WHERE prediction_id=?',(prediction_id,)).fetchone()
                    if old['device_id']!=device_id or media['sha256']!=photo_hash:
                        raise RequestError(409,'prediction_id_reused_with_different_input')
                    return self._prediction(db,prediction_id,language),False
                if self.predictor is None:
                    raise RequestError(503,'model_not_configured')
                try:
                    scores=list(self.predictor(image))
                    if len(scores)!=6 or any(isinstance(s,bool) or not isinstance(s,Real) or
                       not math.isfinite(s) or not 0<=s<=1 for s in scores) or not math.isclose(sum(scores),1,abs_tol=1e-6):
                        raise ValueError
                    scores=[float(s) for s in scores]
                except Exception:
                    raise RequestError(503,'invalid_model_output') from None
                ranked=sorted(range(6),key=lambda i:(-scores[i],i))
                top,second=ranked[:2]
                relative='media/'+prediction_id+'.png'
                target=self.database.parent/relative
                self.media.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if digest(target.read_bytes())!=photo_hash:
                        raise RequestError(409,'orphan_media_conflict')
                else:
                    created_path=target
                    with target.open('xb') as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                db.execute('''INSERT INTO predictions(prediction_id,device_id,image_path,model_id,
                    top_class_id,confidence,second_class_id,second_confidence) VALUES (?,?,?,?,?,?,?,?)''',
                    (prediction_id,device_id,relative,self.model_id,MODEL_CLASS_IDS[top],scores[top],MODEL_CLASS_IDS[second],scores[second]))
                db.execute('INSERT INTO prediction_media VALUES (?,?,?,?)',
                           (prediction_id,photo_hash,image.width,image.height))
                result=self._prediction(db,prediction_id,language)
            return result,True
        except Exception:
            if created_path is not None:
                # Recheck under the write lock: a concurrent retry might already
                # have committed a reference to this file after our rollback.
                with self.connection(write=True) as db:
                    if not db.execute('SELECT 1 FROM predictions WHERE prediction_id=?',(prediction_id,)).fetchone():
                        created_path.unlink(missing_ok=True)
            raise

    def resolve_escalation(self,prediction_id,routed_to='local_adviser'):
        uuid_value(prediction_id)
        if routed_to not in ('local_adviser','extension_service'):
            raise RequestError(400,'invalid_routing_code')
        with self.connection(write=True) as db:
            old=db.execute('SELECT * FROM escalations WHERE prediction_id=?',(prediction_id,)).fetchone()
            if old is None:
                raise RequestError(404,'escalation_not_found')
            if not old['resolved'] or old['routed_to']!=routed_to:
                db.execute('UPDATE escalations SET routed_to=?,resolved=1,resolved_at=? WHERE prediction_id=?',
                           (routed_to,timestamp(),prediction_id))
            return dict(db.execute('SELECT * FROM escalations WHERE prediction_id=?',(prediction_id,)).fetchone())

    def _envelope(self,db,prediction_id):
        p=dict(db.execute('SELECT * FROM predictions WHERE prediction_id=?',(prediction_id,)).fetchone())
        p.pop('synced');p.pop('synced_at')
        e=db.execute('SELECT * FROM escalations WHERE prediction_id=?',(prediction_id,)).fetchone()
        media=dict(db.execute('SELECT * FROM prediction_media WHERE prediction_id=?',(prediction_id,)).fetchone())
        envelope={'prediction':p,'escalation':dict(e) if e else None,'media':media,'is_mock':self.mode=='mock'}
        token=digest(json.dumps(envelope,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
        return envelope,token

    def list_pending_sync(self,limit=20):
        bounded_limit(limit)
        with self.connection() as db:
            queued=db.execute('SELECT * FROM sync_queue ORDER BY created_at,queue_id LIMIT ?',(limit,)).fetchall()
            result=[]
            for row in queued:
                envelope,token=self._envelope(db,row['row_id'])
                result.append(dict(row,envelope=envelope,sync_token=token))
            return result

    def acknowledge_sync(self,prediction_id,sync_token,error_code=None):
        uuid_value(prediction_id)
        if not isinstance(sync_token,str) or len(sync_token)!=64:
            raise RequestError(400,'invalid_sync_token')
        if error_code not in (None,'network_unavailable','timeout','server_error','unauthorized'):
            raise RequestError(400,'invalid_error_code')
        with self.connection(write=True) as db:
            if not db.execute('SELECT 1 FROM predictions WHERE prediction_id=?',(prediction_id,)).fetchone():
                raise RequestError(404,'prediction_not_found')
            _,current=self._envelope(db,prediction_id)
            if current!=sync_token:
                raise RequestError(409,'stale_sync_acknowledgement')
            if error_code:
                db.execute('UPDATE sync_queue SET attempts=attempts+1,last_error=? WHERE row_id=?',
                           (error_code,prediction_id))
                return {'acknowledged':False}
            if db.execute('SELECT 1 FROM sync_queue WHERE row_id=?',(prediction_id,)).fetchone():
                db.execute('UPDATE predictions SET synced=1,synced_at=? WHERE prediction_id=?',(timestamp(),prediction_id))
                db.execute('DELETE FROM sync_queue WHERE row_id=?',(prediction_id,))
            return {'acknowledged':True}
