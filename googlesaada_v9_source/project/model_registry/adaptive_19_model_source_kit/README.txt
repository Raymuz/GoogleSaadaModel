GOOGLESAADA V9 — ONE-UPLOAD ADAPTIVE MODEL BUNDLE
=================================================
This package consolidates 19 UNIQUE model binaries:
 - 10 historical specialist models from the 13-model backup
 - 3 historical worldwide models from the 13-model backup
 - 6 Stage 5B/5C models (Soccer: LGBM/XGB/CatBoost; NFL: LGBM/XGB/CatBoost)
It does NOT contain six V9 sport-specific models covering NBA/NCAA/WNBA.

All original archives have passed ZIP CRC checking; model binary SHA256 is
in manifest.json. Model files are historical input checkpoints, NOT guaranteed
training-compatible. No models were deserialized/executed by packaging.

IMPORTANT:
  * The old worldwide/specialist models may use different tasks, features,
    target encodings and historical data. Do not initialize V9 classifiers
    from them merely because their extension is .joblib.
  * V9 Stage5B5C artifacts also carry source-period training contracts: do
    not assume they match current 37/34 restricted-feature V9 stage.
  * Re-fitting same data or adding trees does not guarantee improvement.
  * Preserve original checkpoints and validate on chronological holdouts.
  * Never load untrusted joblib/pickle files: deserialization may execute code.

Run COLAB_IMPORT_ONCE.py in Colab to upload and copy bundle to Google Drive.
Future adaptive-stage scripts should reference the persisted Drive path; no
repeat upload required. Import script never deserializes models or starts training.

Any fresh per-sport model checkpoints completed AFTER these two archives must
be added later as separate versions; they are NOT present in this package.
