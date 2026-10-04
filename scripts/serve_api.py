"""Authenticated local WSGI API. Built-in server is for development only."""
import argparse
import base64
import binascii
import hmac
import json
import os
import sqlite3
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, make_server

from runtime_db import ROOT, RequestError, Runtime, initialize_runtime
from class_mapping import MESSAGES, fallback, error_fallback

MAX_BODY = 12 * 1024 * 1024


def exact_fields(body,required,optional=()):
    if not isinstance(body,dict) or set(body)-set(required)-set(optional) or set(required)-set(body):
        raise RequestError(400,'invalid_request_fields')


class API:
    def __init__(self,runtime,token):
        if not isinstance(token,str) or len(token)<32 or not token.isascii():
            raise ValueError('Set a random ASCII API token of at least 32 characters')
        self.runtime,self.token=runtime,token

    def __call__(self,environ,start_response):
        status=200
        language='en'
        try:
            path=environ.get('PATH_INFO','')
            method=environ.get('REQUEST_METHOD','GET')
            if path=='/health' and method=='GET':
                result={'status':'ok','inference_ready':self.runtime.predictor is not None,
                        'mode':self.runtime.mode,'is_mock':self.runtime.mode=='mock'}
            else:
                supplied=environ.get('HTTP_AUTHORIZATION','')
                if not supplied.isascii() or not hmac.compare_digest(supplied,'Bearer '+self.token):
                    raise RequestError(401,'authentication_required')
                query=parse_qs(environ.get('QUERY_STRING',''),keep_blank_values=True)
                if any(len(values)!=1 for values in query.values()):
                    raise RequestError(400,'duplicate_query_parameter')
                query={key:values[0] for key,values in query.items()}
                language=query.get('language','en')
                body=None
                if method=='POST':
                    if environ.get('CONTENT_TYPE','').split(';')[0].strip()!='application/json':
                        raise RequestError(415,'expected_application_json')
                    length=int(environ.get('CONTENT_LENGTH') or 0)
                    if not 0<length<=MAX_BODY:
                        raise RequestError(413,'request_size_limit')
                    raw=environ['wsgi.input'].read(length)
                    if len(raw)!=length:
                        raise RequestError(400,'incomplete_request')
                    body=json.loads(raw)
                    if isinstance(body,dict):
                        language=body.get('language',language)
                result,status=self.route(method,path,query,body)
        except RequestError as error:
            status=error.status;result={'error':error.code}
        except (ValueError,TypeError,KeyError,UnicodeError,binascii.Error):
            status=400;result={'error':'invalid_request'}
        except sqlite3.OperationalError:
            status=503;result={'error':'database_temporarily_unavailable'}
        except Exception:
            # Do not return database details, request bodies or filesystem paths.
            status=500;result={'error':'internal_error'}
        if status>=400:
            result['fallback']=error_fallback(result['error'],language)
        payload=json.dumps(result,ensure_ascii=False,allow_nan=False).encode('utf-8')
        headers=[('Content-Type','application/json; charset=utf-8'),('Content-Length',str(len(payload))),
                 ('Cache-Control','no-store'),('X-Content-Type-Options','nosniff')]
        if status==401:
            headers.append(('WWW-Authenticate','Bearer'))
        start_response(f'{status} {HTTPStatus(status).phrase}',headers)
        return [payload]

    def route(self,method,path,query,body):
        runtime=self.runtime
        if method=='GET' and path=='/classes':
            return runtime.classes(query.get('language','en')),200
        if method=='GET' and path=='/fallbacks':
            language=query.get('language','en')
            return [fallback(code,language) for code in MESSAGES],200
        if method=='GET' and path=='/advice-status':
            return runtime.advice_status(int(query['class_id']),query.get('language','en')),200
        if method=='GET' and path=='/advice':
            return runtime.get_reviewed_advice(int(query['class_id']),query.get('language','en')),200
        if method=='POST' and path=='/predictions':
            exact_fields(body,['prediction_id','device_id','image_base64'],['language'])
            if not isinstance(body['image_base64'],str):
                raise RequestError(400,'invalid_image_base64')
            try:
                data=base64.b64decode(body['image_base64'],validate=True)
            except (binascii.Error,ValueError):
                raise RequestError(400,'invalid_image_base64') from None
            result,created=runtime.submit_image(body['prediction_id'],body['device_id'],data,body.get('language','en'))
            return result,201 if created else 200
        if method=='GET' and path=='/predictions':
            return runtime.list_predictions(query['device_id'],int(query.get('limit',20)),
                                             int(query.get('offset',0)),query.get('language','en')),200
        if method=='GET' and path.startswith('/predictions/'):
            return runtime.get_prediction(path.removeprefix('/predictions/'),query.get('language','en')),200
        if method=='POST' and path=='/escalations/resolve':
            exact_fields(body,['prediction_id'],['routed_to'])
            return runtime.resolve_escalation(body['prediction_id'],body.get('routed_to','local_adviser')),200
        if method=='GET' and path=='/sync/pending':
            return runtime.list_pending_sync(int(query.get('limit',20))),200
        if method=='POST' and path in ('/sync/ack','/sync/failure'):
            required=['prediction_id','sync_token']+(['error_code'] if path=='/sync/failure' else [])
            exact_fields(body,required)
            return runtime.acknowledge_sync(body['prediction_id'],body['sync_token'],body.get('error_code')),200
        if method not in ('GET','POST'):
            raise RequestError(405,'method_not_allowed')
        raise RequestError(404,'route_not_found')


class QuietHandler(WSGIRequestHandler):
    def log_message(self,format,*args):
        pass  # Do not log URLs containing installation IDs or any image payloads.


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    init=commands.add_parser('init')
    init.add_argument('--catalog',type=Path,default=ROOT/'artifacts/databases/coffee.sqlite')
    init.add_argument('--database',type=Path,default=ROOT/'artifacts/runtime/coffee.sqlite')
    init.add_argument('--mock',action='store_true')
    serve=commands.add_parser('serve')
    serve.add_argument('--database',type=Path,default=ROOT/'artifacts/runtime/coffee.sqlite')
    serve.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    try:
        if args.command=='init':
            initialize_runtime(args.catalog,args.database,args.mock)
            print(f'Initialized {args.database}; mode={"mock" if args.mock else "unconfigured"}')
        else:
            app=API(Runtime(args.database),os.environ.get('COFFEE_API_TOKEN',''))
            with make_server('127.0.0.1',args.port,app,handler_class=QuietHandler) as server:
                print(f'Local development API: http://127.0.0.1:{args.port}; mode={app.runtime.mode}',flush=True)
                server.serve_forever()
    except (ValueError,OSError,sqlite3.Error) as error:
        parser.exit(1,f'Error: {error}\n')
    except KeyboardInterrupt:
        pass


if __name__=='__main__':
    main()
