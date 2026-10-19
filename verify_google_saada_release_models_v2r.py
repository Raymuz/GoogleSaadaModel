from pathlib import Path
from hashlib import sha256
import joblib, sklearn
ROOT=Path(__file__).resolve().parent
PINS={'deployment_files/models/ft_away_poisson_safe_v2.joblib': 'f9240500824ada393777c9bccaa3b82b17d6b9a04ee83da969fda31826a6f0ca', 'deployment_files/models/ft_home_poisson_safe_v2.joblib': '46d11c9955c6438e5abc1beef07f05cef25afcc601c23645bd9a4eed265e9cb3', 'deployment_files/models/ht_away_poisson_safe_v2.joblib': '18a24266328db87f5136d99b7fc72bb623d96a88edb668f69c040ef0b52ff4e4', 'deployment_files/models/ht_home_poisson_safe_v2.joblib': '860879c5079e4c7a0e62d2d248599a921c7ff4f6065e394c20430d786aff421b', 'deployment_files/models/sh_away_poisson_safe_v2.joblib': 'e21e65833be93f52f0127433cf7ab17dbdb57c834babbfcb81e988726914c8c5', 'deployment_files/models/sh_home_poisson_safe_v2.joblib': 'd2a87c94cd52ae0a16930b6636abee87223ac1544849462443485e5381293cad', 'deployment_files/models/elo_only_safe_v2.joblib': 'a02ab47b800a4c9c1cc7d57bcce3234624c0b7fcb4b613dd1331716ba83fa306', 'deployment_files/models/logistic_regression_safe_v2.joblib': '06c91add3986324f23c435ec78ff0003f8792e4b36e1eebe89fa9ba39d838213', 'deployment_files/models/random_forest_safe_v2.joblib': '5aa1dc5d05c561349a95d79a5d68dc9d6325c14098fd2c89c543567e3fb09c9e', 'deployment_files/models/xgboost_safe_v2.joblib': 'c6dc550c119279cc05be25983fb19e25564f4d2b9698154ad51b94a38d12bf03', 'global_v3/v2g_staging/models/worldwide_match_result.joblib': 'd4ccad9a98706953d49810442d1049b7b1f21b5b667109e1e538b9c5edc9f360', 'global_v3/v2g_staging/models/worldwide_over_2_5.joblib': '17f39b918858e3b19fbace73596007b111946713cd8ab2a39bae625ddeeaceec', 'global_v3/v2g_staging/models/worldwide_btts.joblib': '4d340e2056def4b4a1c41b6a407a34850c20696ed3b01dae24563bdc965c1f15'}
assert sklearn.__version__=='1.6.1', sklearn.__version__
def sha(p):
 h=sha256()
 with p.open('rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
assert len(PINS)==13
for rel,want in PINS.items():
 p=ROOT/rel
 assert p.is_file() and not p.is_symlink() and sha(p)==want, rel
print('[PASS] 13 original trained-model file fingerprints')
for rel in PINS:
 if rel.startswith('global_v3/v2g_staging/models/worldwide_'):
  model=joblib.load(ROOT/rel)
  assert callable(getattr(model,'predict_proba',None)),rel
print('[PASS] 3 worldwide models load under sklearn 1.6.1')
