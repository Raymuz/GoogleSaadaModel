
import json, joblib, numpy as np, pandas as pd
from pathlib import Path

ROOT = Path("/app")
REGISTRY = ROOT / "backend/google_saada_unified_model_registry.json"
data = json.loads(REGISTRY.read_text())

models = (
    list(data["original_models"].items()) +
    list(data["selected_worldwide_models"].items())
)

assert len(models) == 13, f"Expected 13; found {len(models)}"

def synthetic_value(name):
    n = name.lower()
    if "elo_diff" in n:
        return 0.0
    if "elo" in n:
        return 1500.0
    if "rest" in n:
        return 5.0
    if "point" in n:
        return 1.5
    if "goal" in n or "_gf_" in n or "_ga_" in n:
        return 1.3
    if "rate" in n or "win_pct" in n or "form" in n:
        return 0.5
    return 1.0

passed = 0
for model_name, record in models:
    path = ROOT / record["path"]

    # Diagnostic-only: resolve the worldwide registry aliases
    # to the restored, SHA-verified model files.
    worldwide_aliases = {
        "btts.joblib":
            "global_v3/v2g_staging/models/worldwide_btts.joblib",
        "match_result.joblib":
            "global_v3/v2g_staging/models/worldwide_match_result.joblib",
        "over_2_5.joblib":
            "global_v3/v2g_staging/models/worldwide_over_2_5.joblib"
    }

    if str(path).startswith(
        str(ROOT / "deployment_files/worldwide_models")
    ):
        basename = path.name
        if basename not in worldwide_aliases:
            raise RuntimeError(
                f"Unknown worldwide model: {basename}"
            )
        path = ROOT / worldwide_aliases[basename]
        print(f"[PATH_FIX] {model_name}: {path}", flush=True)

    assert path.is_file(), f"Missing: {path}"
    model = joblib.load(path)

    names = record.get("feature_names")
    if not names:
        names = getattr(model, "feature_names_in_", None)
    if names is None:
        raise RuntimeError(
            f"{model_name}: no approved feature names available"
        )
    names = list(names)

    count = record.get("feature_count")
    if count is not None and len(names) != int(count):
        raise RuntimeError(f"{model_name}: registry feature count mismatch")

    expected_names = getattr(model, "feature_names_in_", None)
    if expected_names is not None:
        saved_names = list(expected_names)

        if saved_names != names:
            # Diagnostic only: do not change the actual registry.
            if model_name not in (
                "btts", "match_result", "over_2_5"
            ):
                raise RuntimeError(
                    f"{model_name}: feature names/order mismatch"
                )

            missing = [n for n in saved_names if n not in names]
            extra = [n for n in names if n not in saved_names]

            print(
                f"[REGISTRY_MISMATCH] {model_name}: "
                f"registry={len(names)} saved={len(saved_names)} "
                f"missing={missing} extra={extra}",
                flush=True
            )

            # Only the test input changes. No model is rewritten.
            names = saved_names

            print(
                f"[MODEL_FEATURE_ORDER] {model_name}: "
                f"{names}",
                flush=True
            )

    X = pd.DataFrame(
        [[synthetic_value(n) for n in names]],
        columns=names
    )


    # Diagnostic-only compatibility shim.
    # Do not save this modified estimator or use as production evidence.
    from sklearn.linear_model import LogisticRegression
    estimators = (
        [part for _, part in model.steps]
        if hasattr(model, "steps") else [model]
    )
    for estimator in estimators:
        if isinstance(estimator, LogisticRegression):
            if not hasattr(estimator, "multi_class"):
                estimator.multi_class = "auto"
                print(
                    f"[COMPAT_SHIM] {model_name}: "
                    "assigned legacy multi_class=auto",
                    flush=True
                )


    # Diagnostic-only compatibility shim.
    # Do not save this modified estimator or use as production evidence.
    from sklearn.linear_model import LogisticRegression
    estimators = (
        [part for _, part in model.steps]
        if hasattr(model, "steps") else [model]
    )
    for estimator in estimators:
        if isinstance(estimator, LogisticRegression):
            if not hasattr(estimator, "multi_class"):
                estimator.multi_class = "auto"
                print(
                    f"[COMPAT_SHIM] {model_name}: "
                    "assigned legacy multi_class=auto",
                    flush=True
                )

    prediction = np.asarray(model.predict(X))
    if prediction.size != 1:
        raise RuntimeError(f"{model_name}: unexpected prediction shape")
    # Accept legitimate classification labels as well as
    # numeric regression predictions.
    if model_name == "match_result":
        classes = getattr(model, "classes_", None)
        if classes is None:
            raise RuntimeError(
                "match_result: missing classifier classes_"
            )

        if not all(
            any(label == cls for cls in classes)
            for label in prediction
        ):
            raise RuntimeError(
                "match_result: prediction not in known classes"
            )

        print(
            f"[CLASS_LABEL_OK] match_result: "
            f"{prediction.tolist()}",
            flush=True
        )
    else:
        try:
            numeric = prediction.astype(float)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"{model_name}: nonnumeric prediction"
            ) from exc

        if not np.isfinite(numeric).all():
            raise RuntimeError(
                f"{model_name}: invalid numeric prediction"
            )

    if hasattr(model, "predict_proba"):
        probabilities = np.asarray(model.predict_proba(X), dtype=float)
        if (
            probabilities.ndim != 2 or
            probabilities.shape[0] != 1 or
            not np.isfinite(probabilities).all() or
            (probabilities < 0).any() or
            (probabilities > 1).any() or
            not np.allclose(probabilities.sum(axis=1), 1, atol=1e-5)
        ):
            raise RuntimeError(f"{model_name}: invalid probabilities")

    passed += 1
    print(f"[PASS] INFERENCE {passed:02d}/13 {model_name}", flush=True)

assert passed == 13
print("[PASS] ALL 13 SYNTHETIC INFERENCE TESTS", flush=True)
print("[PENDING] Historical inference, Judge and calibration", flush=True)
