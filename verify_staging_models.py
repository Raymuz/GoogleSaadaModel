from pathlib import Path
import joblib
paths = ['deployment_files/models/elo_only_safe_v2.joblib', 'deployment_files/models/ft_away_poisson_safe_v2.joblib', 'deployment_files/models/ft_home_poisson_safe_v2.joblib', 'deployment_files/models/ht_away_poisson_safe_v2.joblib', 'deployment_files/models/ht_home_poisson_safe_v2.joblib', 'deployment_files/models/logistic_regression_safe_v2.joblib', 'deployment_files/models/random_forest_safe_v2.joblib', 'deployment_files/models/sh_away_poisson_safe_v2.joblib', 'deployment_files/models/sh_home_poisson_safe_v2.joblib', 'deployment_files/models/xgboost_safe_v2.joblib', 'global_v3/v2g_staging/models/worldwide_btts.joblib', 'global_v3/v2g_staging/models/worldwide_match_result.joblib', 'global_v3/v2g_staging/models/worldwide_over_2_5.joblib']
passed = 0
for name in paths:
    try:
        path = Path('/app') / name
        if not path.is_file():
            raise FileNotFoundError(name)
        model = joblib.load(path)
        if not (hasattr(model, 'predict') or hasattr(model, 'predict_proba')):
            raise TypeError('No prediction method')
        passed += 1
        print(f'[PASS] {passed:02d}/13 {name}', flush=True)
    except Exception as exc:
        print(f'[FAIL] {name}: {type(exc).__name__}: {exc}', flush=True)
        raise
assert passed == 13
print('[PASS] ALL 13 MODELS LOADED', flush=True)
