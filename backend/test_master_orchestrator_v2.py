import unittest, sys
from datetime import datetime,timezone,timedelta
from google_saada_master_orchestrator_v2 import *
NOW=datetime(2026,10,12,tzinfo=timezone.utc)
REG={'original_models':{k:{} for k in ORIGINAL},'selected_worldwide_models':{k:{} for k in WORLDWIDE},'live_routing_enabled':False}
HASH='a'*64

def fixture(i='a'):
    return dict(fixture_id=i,source='test',source_fixture_id=i,home_id='H'+i,away_id='A'+i,home='Home',away='Away',kickoff_utc=(NOW+timedelta(days=1)).isoformat(),verified=True)
def evidence(i='a',specialist_features=None):
    return dict(fixture_id=i,source_fixture_id=i,home_id='H'+i,away_id='A'+i,feature_cutoff_utc=(NOW-timedelta(hours=1)).isoformat(),verified=True,data_source='test_verified_fixture',specialist_features=specialist_features or {})

def adapter(**kw): return dict(specialist='worldwide_match_result',market='match_result',value={'H':.5,'D':.3,'A':.2},evidence_id='test-evidence',artifact_sha256=HASH)
class Suite(unittest.TestCase):
    def test_13_registry(self):self.assertEqual(len(SPECIALISTS),13)
    def test_16(self):verify_request([fixture(str(x)) for x in range(16)],{str(x):evidence(str(x)) for x in range(16)},NOW)
    def test_17(self):
        with self.assertRaises(OrchestrationError):verify_request([fixture(str(x)) for x in range(17)],{},NOW)
    def test_duplicate(self):
        with self.assertRaises(OrchestrationError):verify_request([fixture(),fixture()],{'a':evidence()},NOW)
    def test_started(self):
        f=fixture();f['kickoff_utc']=(NOW-timedelta(minutes=1)).isoformat()
        with self.assertRaises(OrchestrationError):verify_request([f],{'a':evidence()},NOW)
    def test_future_evidence(self):
        e=evidence();e['feature_cutoff_utc']=(NOW+timedelta(minutes=1)).isoformat()
        with self.assertRaises(OrchestrationError):verify_request([fixture()],{'a':e},NOW)
    def test_wrong_identity(self):
        e=evidence();e['home_id']='wrong'
        with self.assertRaises(OrchestrationError):verify_request([fixture()],{'a':e},NOW)
    def test_missing_adapter(self):
        r=MasterOrchestratorV2(REG,adapters={}).execute([fixture()],{'a':evidence()},now_utc=NOW,requested_markets=('match_result',))
        self.assertEqual(r['coverage']['a']['match_result'],'UNAVAILABLE')
    def test_one_result(self):
        r=MasterOrchestratorV2(REG,adapters={'worldwide_match_result':adapter}).execute([fixture()],{'a':evidence(specialist_features={'worldwide_match_result':{'valid':1}})},now_utc=NOW,requested_markets=('match_result',))
        self.assertEqual(r['coverage']['a']['match_result'],'AVAILABLE');self.assertEqual(len(r['whiteboard']),1)
    def test_bad_class_mapping(self):
        board=SpecialistWhiteboard(['a'])
        with self.assertRaises(OrchestrationError):board.publish(fixture_id='a',specialist='worldwide_match_result',market='match_result',value={'A':.8,'D':.2},evidence_id='test',artifact_sha256=HASH)
    def test_poisson_nonnegative(self):
        board=SpecialistWhiteboard(['a']);board.publish(fixture_id='a',specialist='ft_home_poisson',market='ft_home_expected_goals',value=1.5,evidence_id='test',artifact_sha256=HASH)
        self.assertEqual(board.coverage('a',['ft_home_expected_goals'])['ft_home_expected_goals'],'AVAILABLE')
    def test_snapshot_immutable(self):
        board=SpecialistWhiteboard(['a']);board.publish(fixture_id='a',specialist='worldwide_match_result',market='match_result',value={'H':.5,'D':.3,'A':.2},evidence_id='test',artifact_sha256=HASH)
        snap=board.snapshot();snap[0]['value']['H']=0
        self.assertEqual(board.snapshot()[0]['value']['H'],.5)
    def test_duplicate_publication(self):
        board=SpecialistWhiteboard(['a']);d=dict(fixture_id='a',specialist='worldwide_match_result',market='match_result',value={'H':.5,'D':.3,'A':.2},evidence_id='test',artifact_sha256=HASH);board.publish(**d)
        with self.assertRaises(OrchestrationError):board.publish(**d)
    def test_unapproved_registry(self):
        bad={**REG,'live_routing_enabled':True}
        with self.assertRaises(OrchestrationError):MasterOrchestratorV2(bad,adapters={})
    def test_partial_fail(self):
        def broken(**kw):raise RuntimeError('bad')
        r=MasterOrchestratorV2(REG,adapters={'worldwide_match_result':broken}).execute([fixture()],{'a':evidence(specialist_features={'worldwide_match_result':{'x':1}})},now_utc=NOW,requested_markets=('match_result',))
        self.assertEqual(r['coverage']['a']['match_result'],'UNAVAILABLE');self.assertTrue(r['failures'])
if __name__=='__main__': unittest.main(verbosity=2)
