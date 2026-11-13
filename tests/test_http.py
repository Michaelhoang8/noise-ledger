import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from run import Handler
from service import Store,example


class HTTPTests(unittest.TestCase):
    def test_release_cache_exhaustion_and_operator_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=Store(Path(tmp)/'noise.db',lambda n,d:0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def call(method,path,body=None,headers=None):
                c=HTTPConnection(*server.server_address,timeout=5);payload=json.dumps(body) if body is not None else None
                c.request(method,path,payload,headers or ({'Content-Type':'application/json'} if payload else {}));r=c.getresponse();data=r.read();status=r.status;c.close();return status,json.loads(data) if data else None
            try:
                status,data=call('POST','/api/datasets',example());self.assertEqual(status,201);did=data['dataset_id'];path=f'/api/datasets/{did}/release'
                q={'kind':'count','region':None,'level':'half'}
                self.assertEqual(call('POST',path,q)[0],201);self.assertEqual(call('POST',path,q)[0],200)
                self.assertEqual(call('POST',path,q|{'region':'north'})[0],201);self.assertEqual(call('POST',path,q|{'region':'east'})[0],429)
                self.assertEqual(call('GET',f'/api/datasets/{did}/operator')[1]['record_count'],200)
                self.assertEqual(call('HEAD','/api/datasets')[1],None)
                self.assertEqual(call('GET','/service.py')[0],404)
                self.assertEqual(call('GET','/api/datasets',headers={'Host':'example.invalid'})[0],400)
                self.assertEqual(call('POST',path,q,{'Content-Type':'application/json','Origin':'https://other.invalid'})[0],403)
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
