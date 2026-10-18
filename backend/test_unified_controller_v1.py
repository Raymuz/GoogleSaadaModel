import importlib.util,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
P=Path(__file__).with_name('google_saada_unified_controller_v1.py')
s=importlib.util.spec_from_file_location('controller_test',P); c=importlib.util.module_from_spec(s);s.loader.exec_module(c)
NOW=datetime(2026,10,12,tzinfo=timezone.utc)
class Suite(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
  self.file=Path(self.tmp.name)/'registry.json'
  self.names=['a','b'];self.sha='a'*64
  self.file.write_text(json.dumps(dict(maximum_matches=16,original_models={str(i):{} for i in range(10)},live_routing_enabled=False,selected_worldwide_models={m:dict(production_approved=False,feature_names=self.names,required_class_order=(['H','D','A'] if m=='match_result' else [0,1]),sha256=self.sha,version='Step6') for m in c.MARKETS})))
  self.fixture=dict(fixture_id='f1',source='trusted-feed',source_fixture_id='e1',home_id='a',away_id='b',home='A',away='B',kickoff_utc=(NOW+timedelta(hours=5)).isoformat(),verified=True)
  self.ev=dict(fixture_id='f1',source_fixture_id='e1',home_id='a',away_id='b',feature_cutoff_utc=(NOW-timedelta(hours=2)).isoformat(),data_source='historic-matches',verified=True,market_features={m:dict(a=1.,b=2.) for m in c.MARKETS})
 def predict(self,m,row,registry):
  return dict(market=m,probabilities=({'H':.5,'D':.3,'A':.2} if m=='match_result' else {'0':.4,'1':.6}),model_sha256=self.sha,staging_only=True)
 def run_batch(self,fs=None,ev=None,**kw):return c.analyze_verified_batch(fs if fs is not None else [self.fixture],ev if ev is not None else {'f1':self.ev},self.file,now_utc=NOW,predictor=self.predict,**kw)
 def test_valid_three_markets(self):
  out=self.run_batch();self.assertEqual(out['count_complete'],1);self.assertEqual(len(out['results'][0]['markets']),3)
 def test_no_feature_data(self):self.assertEqual(self.run_batch(ev={})['results'][0]['status'],'UNAVAILABLE')
 def test_unverified_fixture(self):
  f={**self.fixture,'verified':False};self.assertEqual(self.run_batch(fs=[f])['count_complete'],0)
 def test_wrong_identity(self):
  e={**self.ev,'away_id':'wrong'};self.assertEqual(self.run_batch(ev={'f1':e})['count_complete'],0)
 def test_postkickoff_features(self):
  e={**self.ev,'feature_cutoff_utc':(NOW+timedelta(hours=6)).isoformat()};self.assertEqual(self.run_batch(ev={'f1':e})['count_complete'],0)
 def test_started_fixture(self):
  f={**self.fixture,'kickoff_utc':(NOW-timedelta(seconds=1)).isoformat()};self.assertEqual(self.run_batch(fs=[f])['count_complete'],0)
 def test_missing_market_features(self):
  e={**self.ev,'market_features':{'match_result':dict(a=1.,b=2.)}};self.assertEqual(self.run_batch(ev={'f1':e})['results'][0]['status'],'PARTIAL')
 def test_sixteen_maximum(self):
  with self.assertRaises(c.ContractViolation):self.run_batch(fs=[self.fixture]*17)
 def test_duplicate_ids(self):
  with self.assertRaises(c.ContractViolation):self.run_batch(fs=[self.fixture]*2)
 def test_no_model_adapter(self):
  with self.assertRaises(c.ContractViolation):c.analyze_verified_batch([self.fixture],{'f1':self.ev},self.file,now_utc=NOW)
 def test_no_live_switch(self):
  data=json.loads(self.file.read_text());data['live_routing_enabled']=True;self.file.write_text(json.dumps(data))
  with self.assertRaises(c.ContractViolation):self.run_batch()
 def test_no_invalid_probabilities(self):
  def wrong(m,row,reg):return dict(market=m,probabilities={'H':2.,'D':-1.,'A':0.},model_sha256=self.sha,staging_only=True)
  with self.assertRaises(c.ContractViolation):c.analyze_verified_batch([self.fixture],{'f1':self.ev},self.file,now_utc=NOW,predictor=wrong,markets=['match_result'])
if __name__=='__main__':unittest.main(verbosity=2)
