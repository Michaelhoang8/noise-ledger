import concurrent.futures
import json
import hashlib
import subprocess
import math
import random
import sqlite3
import tempfile
import threading
import unittest
from fractions import Fraction
from pathlib import Path
from service import Store, example, LEVELS, REGIONS, geometric_noise, BudgetExceeded, Conflict, digest, normalize_query, normalize_dataset


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'ledger.sqlite3';self.calls=0;self.lock=threading.Lock()
        def noise(n,d):
            with self.lock:self.calls+=1
            return -2
        self.store=Store(self.path,noise);self.id=self.store.seal(example())['dataset_id']

    def tearDown(self):self.tmp.cleanup()

    def query(self,region=None,kind='count',level='half'):return {'kind':kind,'region':region,'level':level}

    def test_release_cache_exact_budget_and_restart(self):
        a=self.store.release(self.id,self.query());self.assertEqual(a['release']['values'],{'count':'198'})
        replay=self.store.release(self.id,self.query());self.assertTrue(replay['replayed']);self.assertEqual(a['release'],replay['release']);self.assertEqual(self.calls,1)
        b=self.store.release(self.id,self.query(kind='histogram'));self.assertEqual(b['release']['values'],{'north':'69','south':'51','east':'40','west':'32'})
        self.assertEqual(b['release']['after']['numerator'],'4');self.assertEqual(self.calls,5)
        with self.assertRaises(BudgetExceeded):self.store.release(self.id,self.query('north'))
        self.assertTrue(Store(self.path).release(self.id,self.query())['replayed'])
        self.assertEqual(len(self.store.ledger(self.id)['entries']),2)

    def test_rational_composition_does_not_round_budget_decisions(self):
        data=example();data['records']=data['records'][:-1];data['budget_factor']=2;did=self.store.seal(data)['dataset_id']
        self.store.release(did,self.query(level='three_quarters'))
        self.store.release(did,self.query('north',level='three_quarters'))
        self.store.release(did,self.query('south',level='nine_tenths'))
        spent=self.store.ledger(did)['spent'];self.assertEqual((spent['numerator'],spent['denominator']),('160','81'))
        with self.assertRaises(BudgetExceeded):self.store.release(did,self.query('east',level='nine_tenths'))

    def test_browser_round_trip_preserves_hashable_payload(self):
        fresh=self.store.release(self.id,self.query())
        cached=self.store.release(self.id,self.query())
        entry=self.store.ledger(self.id)['entries'][0]
        for wrapper in (fresh,cached,entry):
            exported=json.loads(subprocess.check_output(['node','-e',"let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s))));"],input=json.dumps(wrapper),text=True))
            self.assertEqual(hashlib.sha256(exported['canonical_payload'].encode()).hexdigest(),exported['hash'])
            self.assertEqual(json.loads(exported['canonical_payload']),exported['release'])
            self.assertEqual(exported['canonical_payload'],fresh['canonical_payload'])

    def test_same_records_cannot_reset_a_budget_by_renaming_or_reordering(self):
        self.store.release(self.id,self.query());data=example();data['name']='New name';data['budget_factor']=64;data['records'].reverse()
        replay=self.store.seal(data);self.assertTrue(replay['replayed']);self.assertEqual(replay['dataset_id'],self.id)
        self.assertEqual(self.store.operator(self.id)['ledger']['budget_factor'],4)
        self.assertEqual(len(self.store.list()),1)

    def test_concurrent_duplicate_release_samples_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(lambda _:self.store.release(self.id,self.query()),range(24)))
        self.assertEqual(sum(not r['replayed'] for r in results),1);self.assertEqual(self.calls,1)
        self.assertEqual(len({r['hash'] for r in results}),1)

    def test_concurrent_distinct_queries_cannot_overspend(self):
        queries=[self.query(r) for r in (None,*REGIONS)]+[self.query(kind='histogram')]
        def release(q):
            try:return self.store.release(self.id,q)
            except BudgetExceeded:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(release,queries))
        self.assertEqual(sum(r is not None for r in results),2)
        self.assertEqual(self.store.ledger(self.id)['spent']['numerator'],'4')

    def test_failed_sql_write_rolls_back_release_and_budget(self):
        with self.store.db() as c:c.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON releases BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.Error):self.store.release(self.id,self.query())
        self.assertEqual(self.store.ledger(self.id)['spent']['numerator'],'1')
        with self.store.db() as c:c.execute('DROP TRIGGER fail_insert')
        self.assertEqual(self.store.release(self.id,self.query())['release']['sequence'],1)

    def test_immutable_rows_and_checksum_detection(self):
        self.store.release(self.id,self.query())
        with self.store.db() as c:
            with self.assertRaises(sqlite3.Error):c.execute('DELETE FROM releases')
            with self.assertRaises(sqlite3.Error):c.execute("UPDATE datasets SET name='changed'")
            c.execute('DROP TRIGGER immutable_releases_update');c.execute("UPDATE releases SET payload='{}'")
        with self.assertRaises(Conflict):self.store.ledger(self.id)
        with self.assertRaises(Conflict):self.store.release(self.id,self.query('north'))

    def test_public_release_omits_truth_records_and_private_dataset_fingerprint(self):
        release=self.store.release(self.id,self.query())['release'];text=json.dumps(release)
        for forbidden in ('records','true_histogram','record_count','person-','Fictional field survey','noise_value'):
            self.assertNotIn(forbidden,text)
        self.assertEqual(release['previous_hash'],'0'*64)
        self.assertEqual(self.store.operator(self.id)['true_histogram']['north'],71)

    def test_sealed_dataset_checksum_blocks_even_cached_release_reads(self):
        self.store.release(self.id,self.query())
        with self.store.db() as c:
            c.execute('DROP TRIGGER immutable_datasets_update')
            c.execute("UPDATE datasets SET records='[]'")
        with self.assertRaises(Conflict):self.store.release(self.id,self.query())
        with self.assertRaises(Conflict):self.store.operator(self.id)
        self.assertEqual(self.calls,1)

    def test_empty_and_invalid_datasets_and_queries(self):
        empty=self.store.seal({'name':'Empty','budget_factor':2,'records':[]})['dataset_id'];self.assertEqual(self.store.release(empty,self.query())['release']['values']['count'],'-2')
        for patch in [{'budget_factor':True},{'budget_factor':65},{'records':[{'id':'same','region':'north'}]*2},{'records':[{'id':'a','region':[]}]}]:
            with self.assertRaises(ValueError):normalize_dataset(example()|patch)
        for q in [{'kind':'histogram','region':'north','level':'half'},{'kind':'count','region':None,'level':[]},{'kind':'sum','region':None,'level':'half'}]:
            with self.assertRaises(ValueError):normalize_query(q)


class MechanismTests(unittest.TestCase):
    def test_geometric_difference_and_known_seeded_moments(self):
        stream=iter([0,0,1,0,1]);self.assertEqual(geometric_noise(1,2,lambda _:next(stream)),1)
        for n,d in LEVELS.values():
            rng=random.Random(85);values=[geometric_noise(n,d,rng.randrange) for _ in range(50000)];q=n/d;variance=2*q/(1-q)**2
            mean=sum(values)/len(values);second=sum(x*x for x in values)/len(values)
            self.assertLess(abs(mean),6*math.sqrt(variance/len(values)));self.assertLess(abs(second-variance),variance*.06)

    def test_exact_mass_ratios_satisfy_sensitivity_one_bound(self):
        for n,d in LEVELS.values():
            q=Fraction(n,d);factor=1/q
            def mass(k):return (1-q)/(1+q)*q**abs(k)
            for output in range(-30,31):
                ratio=mass(output)/mass(output-1)
                self.assertLessEqual(ratio,factor);self.assertGreaterEqual(ratio,1/factor)
            # One added record changes one histogram coordinate, so other factors cancel.
            for output in ((0,0,0,0),(-3,2,4,1),(5,-4,3,-2)):
                p=math.prod(mass(x) for x in output);neighbor=math.prod(mass(x-(i==2)) for i,x in enumerate(output))
                self.assertLessEqual(p/neighbor,factor);self.assertGreaterEqual(p/neighbor,1/factor)


if __name__=='__main__':unittest.main()
