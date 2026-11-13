"""Sealed datasets, cached discrete-geometric releases, and exact composition."""
import hashlib
import json
import math
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

REGIONS = ('north', 'south', 'east', 'west')
LEVELS = {'half': (1, 2), 'three_quarters': (3, 4), 'nine_tenths': (9, 10)}


class Conflict(ValueError): pass
class BudgetExceeded(ValueError): pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def factor(value):
    return {'numerator': str(value.numerator), 'denominator': str(value.denominator),
            'epsilon': math.log(value.numerator)-math.log(value.denominator)}


def geometric_noise(numerator, denominator, randbelow=secrets.randbelow):
    """Difference of two geometric counts; exact rational coin probabilities."""
    def draw():
        count = 0
        while randbelow(denominator) < numerator:
            count += 1
        return count
    return draw()-draw()


def example():
    records = []
    for region, count in zip(REGIONS, (71, 53, 42, 34)):
        for _ in range(count):
            records.append({'id': f'person-{len(records)+1:03}', 'region': region})
    return {'name': 'Fictional field survey', 'budget_factor': 4, 'records': records}


def normalize_dataset(raw):
    if not isinstance(raw, dict): raise ValueError('Provide a dataset object.')
    name = raw.get('name')
    if not isinstance(name, str) or not 1 <= len(name) <= 80 or not name.isascii() or any(ord(c)<32 or ord(c)>126 for c in name):
        raise ValueError('Dataset name must contain 1–80 printable ASCII characters.')
    budget = raw.get('budget_factor')
    if type(budget) is not int or not 2 <= budget <= 64:
        raise ValueError('Budget factor must be an integer from 2 to 64.')
    rows = raw.get('records')
    if not isinstance(rows, list) or len(rows)>1000:
        raise ValueError('Use 0–1,000 records, one per person.')
    normalized = []; seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'region'} or not isinstance(row['id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', row['id']) or row['id'] in seen:
            raise ValueError('Each record needs a unique ASCII person ID (1–64 letters/digits/_/-) and a region.')
        if row['region'] not in REGIONS: raise ValueError('Regions must be north, south, east, or west.')
        seen.add(row['id']);normalized.append({'id': row['id'], 'region': row['region']})
    normalized.sort(key=lambda r:r['id'])
    return name, budget, normalized


def normalize_query(raw):
    if not isinstance(raw, dict): raise ValueError('Provide a query object.')
    kind, region, level = raw.get('kind'), raw.get('region'), raw.get('level')
    if kind not in ('count', 'histogram'): raise ValueError('Choose count or histogram.')
    if region is not None and region not in REGIONS: raise ValueError('Choose a fixed public region or all regions.')
    if kind == 'histogram' and region is not None: raise ValueError('Histogram queries use all four public bins.')
    if not isinstance(level,str) or level not in LEVELS: raise ValueError('Choose a supported geometric-noise level.')
    return {'kind':kind, 'region':region, 'level':level}


class Store:
    def __init__(self, path, noise=geometric_noise):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.noise=noise
        with self.db() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, budget INTEGER NOT NULL,
                    records TEXT NOT NULL, digest TEXT UNIQUE NOT NULL, created TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS releases (
                    dataset_id TEXT NOT NULL REFERENCES datasets(id), sequence INTEGER NOT NULL,
                    query_key TEXT NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL,
                    PRIMARY KEY(dataset_id,sequence), UNIQUE(dataset_id,query_key)
                );
                CREATE TRIGGER IF NOT EXISTS immutable_datasets_update BEFORE UPDATE ON datasets BEGIN SELECT RAISE(ABORT,'sealed dataset'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_datasets_delete BEFORE DELETE ON datasets BEGIN SELECT RAISE(ABORT,'sealed dataset'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_releases_update BEFORE UPDATE ON releases BEGIN SELECT RAISE(ABORT,'immutable release'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_releases_delete BEFORE DELETE ON releases BEGIN SELECT RAISE(ABORT,'immutable release'); END;
            ''')

    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
        try:yield c
        finally:c.close()

    def _dataset(self,c,dataset_id):
        if not isinstance(dataset_id,str) or not re.fullmatch('[0-9a-f]{32}',dataset_id):raise ValueError('Invalid dataset ID.')
        row=c.execute('SELECT * FROM datasets WHERE id=?',(dataset_id,)).fetchone()
        if row is None:raise ValueError('Dataset not found.')
        records=json.loads(row['records'])
        if digest(records)!=row['digest']:raise Conflict('Sealed dataset checksum does not match.')
        return row,records

    def _ledger(self,c,row):
        records=c.execute('SELECT * FROM releases WHERE dataset_id=? ORDER BY sequence',(row['id'],)).fetchall()
        product=Fraction(1);previous='0'*64;entries=[]
        for i,r in enumerate(records,1):
            try:p=json.loads(r['payload'])
            except ValueError:raise Conflict('Release ledger contains invalid JSON.') from None
            if not isinstance(p,dict) or digest(p)!=r['hash']:raise Conflict('Release ledger checksum does not match.')
            q=normalize_query(p['query']);num,den=LEVELS[q['level']]
            after=product*Fraction(den,num)
            before_matches = p['before']['numerator']==str(product.numerator) and p['before']['denominator']==str(product.denominator)
            after_matches = p['after']['numerator']==str(after.numerator) and p['after']['denominator']==str(after.denominator)
            if r['sequence']!=i or p['sequence']!=i or p['dataset_id']!=row['id'] or p['previous_hash']!=previous or not before_matches or not after_matches or digest(q)!=r['query_key'] or after>row['budget']:
                raise Conflict('Release ledger integrity check failed.')
            previous=r['hash'];product=after;entries.append({'release':p,'hash':r['hash'],'canonical_payload':r['payload']})
        return {'dataset_id':row['id'],'budget_factor':row['budget'],'budget_epsilon':math.log(row['budget']),'spent':factor(product),'remaining':factor(Fraction(row['budget'])/product),'entries':entries,'audit_ok':True}

    def seal(self,raw):
        name,budget,records=normalize_dataset(raw);sha=digest(records)
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            existing=c.execute('SELECT id FROM datasets WHERE digest=?',(sha,)).fetchone()
            if existing:
                c.execute('COMMIT');return {'dataset_id':existing['id'],'replayed':True}
            if c.execute('SELECT COUNT(*) FROM datasets').fetchone()[0]>=20:raise Conflict('This local store supports at most 20 sealed datasets.')
            dataset_id=uuid.uuid4().hex
            c.execute('INSERT INTO datasets VALUES(?,?,?,?,?,?)',(dataset_id,name,budget,canonical(records),sha,datetime.now(timezone.utc).isoformat()))
            c.execute('COMMIT');return {'dataset_id':dataset_id,'replayed':False}

    def list(self):
        with self.db() as c:
            c.execute('BEGIN')
            rows=c.execute('SELECT * FROM datasets ORDER BY created,id').fetchall()
            return [{'id':r['id'],'name':r['name'],'record_count':len(json.loads(r['records'])),'budget_factor':r['budget']} for r in rows]

    def operator(self,dataset_id):
        with self.db() as c:
            c.execute('BEGIN');row,records=self._dataset(c,dataset_id);ledger=self._ledger(c,row)
            return {'id':row['id'],'name':row['name'],'record_count':len(records),'true_histogram':{region:sum(r['region']==region for r in records) for region in REGIONS},'ledger':ledger}

    def ledger(self,dataset_id):
        with self.db() as c:
            c.execute('BEGIN');row,_=self._dataset(c,dataset_id);return self._ledger(c,row)

    def release(self,dataset_id,raw):
        query=normalize_query(raw);query_key=digest(query)
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE');row,records=self._dataset(c,dataset_id);ledger=self._ledger(c,row)
            cached=c.execute('SELECT payload,hash FROM releases WHERE dataset_id=? AND query_key=?',(dataset_id,query_key)).fetchone()
            if cached:
                c.execute('COMMIT');return {'replayed':True,'release':json.loads(cached['payload']),'hash':cached['hash'],'canonical_payload':cached['payload']}
            current=Fraction(int(ledger['spent']['numerator']),int(ledger['spent']['denominator']))
            num,den=LEVELS[query['level']];after=current*Fraction(den,num)
            if after>row['budget']:raise BudgetExceeded('This new release would exceed the sealed dataset budget. Cached releases remain available.')
            truth={region:sum(r['region']==region for r in records) for region in REGIONS}
            if query['kind']=='histogram':values={region:str(truth[region]+self.noise(num,den)) for region in REGIONS}
            else:
                count=len(records) if query['region'] is None else truth[query['region']]
                values={'count':str(count+self.noise(num,den))}
            seq=len(ledger['entries'])+1
            payload={'format':'noise-ledger-release-v1','dataset_id':dataset_id,'sequence':seq,'query':query,'q':{'numerator':num,'denominator':den},'epsilon':math.log(den/num),'sensitivity':1,'values':values,'before':factor(current),'after':factor(after),'previous_hash':ledger['entries'][-1]['hash'] if ledger['entries'] else '0'*64}
            sha=digest(payload)
            c.execute('INSERT INTO releases VALUES(?,?,?,?,?)',(dataset_id,seq,query_key,canonical(payload),sha))
            c.execute('COMMIT');return {'replayed':False,'release':payload,'hash':sha,'canonical_payload':canonical(payload)}
