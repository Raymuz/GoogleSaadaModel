"""Isolated adapter-interface tests. All probabilities here are TEST DOUBLES."""
import json
import tempfile
import unittest
from pathlib import Path

from google_saada_verified_specialist_adapters_v2 import (
    VerifiedSpecialistAdapterFactory, ALL_ROLES, ORIGINAL_ROLE_MAP, WORLDWIDE_ROLE_MAP,
    AdapterEvidenceError, AdapterArtifactError, sha256
)

class FakeClassifier:
    def __init__(self, classes): self.classes_ = classes
    def predict_proba(self, X):
        assert len(X)==1
        return [[0.2,0.3,0.5]] if len(self.classes_)==3 else [[0.25,0.75]]

class FakePoisson:
    def predict(self, X): return [1.75]

class Suite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        original={}
        worldwide={}
        for role,name in ORIGINAL_ROLE_MAP.items():
            feats=['league_name','home_team_name','away_team_name','elo_diff_pre'] if role=='xgboost' else ['elo_diff_pre']
            original[name]={'path': str(cls.root/name),'sha256':'a'*64,'feature_names':feats,'feature_count':len(feats)}
        for role,name in WORLDWIDE_ROLE_MAP.items():
            worldwide[name]={'path':str(cls.root/name),'sha256':'b'*64,'feature_names':['elo_diff_pre'],'feature_count':1,'required_class_order':['H','D','A'] if name=='match_result' else [0,1]}
        cls.registry=cls.root/'registry.json'
        cls.registry.write_text(json.dumps({'original_models':original,'selected_worldwide_models':worldwide,'maximum_matches':16,'live_routing_enabled':False}))
        cls.factory=VerifiedSpecialistAdapterFactory(cls.registry)
        for role in ALL_ROLES:
            cls.factory._cache[role] = (FakePoisson() if 'poisson' in role else FakeClassifier(['A','D','H'] if role=='worldwide_match_result' else ([0,1] if role in ('worldwide_over_2_5','worldwide_btts') else [0,1,2])))
        cls.fixture={'fixture_id':'f1','source_fixture_id':'s1','home_id':'H1','away_id':'A1','verified':True}
        cls.evidence={**cls.fixture,'evidence_id':'trusted-test-record-1'}
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def row(self,role):
        spec,original=self.factory._spec(role)
        return {key:'Team X' if key in ('league_name','home_team_name','away_team_name') else 123.5 for key in spec['feature_names']}
    def test_thirteen_roles(self): self.assertEqual(len(self.factory.all_adapters()),13)
    def test_original_label_map(self):
        out=self.factory.adapter_for('xgboost')(fixture=self.fixture,evidence=self.evidence,features=self.row('xgboost'))
        self.assertEqual(out['value'],{'H':.5,'D':.3,'A':.2})
    def test_worldwide_label_map(self):
        out=self.factory.adapter_for('worldwide_match_result')(fixture=self.fixture,evidence=self.evidence,features=self.row('worldwide_match_result'))
        self.assertEqual(out['value'],{'H':.5,'D':.3,'A':.2})
    def test_binary(self):
        out=self.factory.adapter_for('worldwide_over_2_5')(fixture=self.fixture,evidence=self.evidence,features=self.row('worldwide_over_2_5'))
        self.assertEqual(out['value'],{'0':.25,'1':.75})
    def test_poisson(self):
        out=self.factory.adapter_for('ft_home_poisson')(fixture=self.fixture,evidence=self.evidence,features=self.row('ft_home_poisson'))
        self.assertEqual(out['value'],1.75)
    def test_bad_identity_rejected(self):
        with self.assertRaises(AdapterEvidenceError):
            self.factory.adapter_for('ft_home_poisson')(fixture={**self.fixture,'home_id':'fake'},evidence=self.evidence,features=self.row('ft_home_poisson'))
    def test_bad_category_rejected(self):
        row=self.row('xgboost');row['league_name']=''
        with self.assertRaises(AdapterEvidenceError):
            self.factory.adapter_for('xgboost')(fixture=self.fixture,evidence=self.evidence,features=row)
    def test_unknown_field_rejected(self):
        row=self.row('xgboost');row['invented_feature']=5
        with self.assertRaises(AdapterEvidenceError):
            self.factory.adapter_for('xgboost')(fixture=self.fixture,evidence=self.evidence,features=row)
    def test_nan_rejected(self):
        row=self.row('worldwide_btts');row['elo_diff_pre']=float('nan')
        with self.assertRaises(AdapterEvidenceError):
            self.factory.adapter_for('worldwide_btts')(fixture=self.fixture,evidence=self.evidence,features=row)
    def test_no_evidence_id(self):
        with self.assertRaises(AdapterEvidenceError):
            self.factory.adapter_for('worldwide_btts')(fixture=self.fixture,evidence={**self.evidence,'evidence_id':''},features=self.row('worldwide_btts'))
    def test_wrong_original_class_order(self):
        self.factory._cache['xgboost']=FakeClassifier(['A','D','H'])
        try:
            with self.assertRaises(AdapterArtifactError):
                self.factory.adapter_for('xgboost')(fixture=self.fixture,evidence=self.evidence,features=self.row('xgboost'))
        finally:self.factory._cache['xgboost']=FakeClassifier([0,1,2])
    def test_no_live_approval(self): self.assertFalse(self.factory.registry['live_routing_enabled'])

if __name__=='__main__':
    unittest.main(verbosity=2)
