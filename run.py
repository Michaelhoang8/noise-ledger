"""Run the Noise Ledger operator workbench on loopback."""
import argparse
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from service import Store, Conflict, BudgetExceeded, example

ROOT=Path(__file__).resolve().parent
ASSETS={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}


class Handler(BaseHTTPRequestHandler):
    def send(self,status,body,mime='application/json'):
        self.send_response(status)
        for k,v in {'Content-Type':mime+'; charset=utf-8','Content-Length':str(len(body)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"}.items():self.send_header(k,v)
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(body)

    def reply(self,status,value):self.send(status,json.dumps(value,allow_nan=False).encode())

    def handle_request(self):
        try:
            self.connection.settimeout(10);hosts=self.headers.get_all('Host',[])
            if len(hosts)!=1 or not re.fullmatch(r'(localhost|127\.0\.0\.1)(:[0-9]{1,5})?',hosts[0],re.I):return self.reply(400,{'error':'Use a loopback Host header.'})
            path=urlsplit(self.path).path;match=re.fullmatch(r'/api/datasets/([0-9a-f]{32})/(operator|ledger|release)',path)
            if self.command in ('GET','HEAD'):
                if path in ASSETS:
                    f,mime=ASSETS[path];return self.send(200,(ROOT/f).read_bytes(),mime)
                if path=='/api/example':return self.reply(200,example())
                if path=='/api/datasets':return self.reply(200,self.server.store.list())
                if match and match[2]=='operator':return self.reply(200,self.server.store.operator(match[1]))
                if match and match[2]=='ledger':return self.reply(200,self.server.store.ledger(match[1]))
                return self.reply(404,{'error':'Not found.'})
            if self.command!='POST':return self.reply(405,{'error':'Use POST.'})
            origin=self.headers.get('Origin')
            if origin is not None and origin not in (f'http://localhost:{self.server.server_port}',f'http://127.0.0.1:{self.server.server_port}'):return self.reply(403,{'error':'Cross-origin request rejected.'})
            lengths=self.headers.get_all('Content-Length',[])
            if self.headers.get('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit() or not 0<int(lengths[0])<=131072:return self.reply(413,{'error':'Use a JSON body of at most 128 KB.'})
            if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':return self.reply(415,{'error':'Use application/json.'})
            body=json.loads(self.rfile.read(int(lengths[0])).decode('utf8'))
            if path=='/api/datasets':
                result=self.server.store.seal(body);return self.reply(200 if result['replayed'] else 201,result)
            if match and match[2]=='release':
                result=self.server.store.release(match[1],body);return self.reply(200 if result['replayed'] else 201,result)
            return self.reply(404,{'error':'Not found.'})
        except BudgetExceeded as e:self.reply(429,{'error':str(e)})
        except Conflict as e:self.reply(409,{'error':str(e)})
        except (ValueError,UnicodeError,RecursionError) as e:self.reply(400,{'error':str(e)})
        except sqlite3.Error:self.reply(503,{'error':'The ledger is unavailable; try again shortly.'})
        except TimeoutError:self.close_connection=True

    do_GET=do_HEAD=do_POST=do_PUT=do_DELETE=handle_request


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8000);parser.add_argument('--data',type=Path,default=ROOT/'data'/'noise.sqlite3');args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.store=Store(args.data)
    print(f'Noise Ledger: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
