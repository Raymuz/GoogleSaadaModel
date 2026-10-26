# GoogleSaada V9 — Stage 4 data milestone
Generated UTC: 2026-10-26T02:38:57.417891+00:00
Source development split: /content/GoogleSaada_V9/data/feature_engineering/stage4uv_labels_splits_audit/20261026T023105Z
Stage 4U+4V receipt reported integrity PASS for six sports.
This viewer independently checks development-file existence, row counts, feature schema and sample row-ID alignment.
Holdout files are never opened. It uses only their row counts from the source receipt.
The original 19 models remain untouched; next stage uploads and inventories their artifacts.
Improvement vs earlier 4.8G is a coverage comparison, not necessarily identical-source or equivalent-split comparison.
Soccer lower total warrants separate source/split difference analysis; no data is silently appended.
Do not load untrusted pickle/joblib artifacts without appropriate sandboxing and provenance review.

## Six-sport summary
     sport  earlier_split_rows  stage4uv_rows  delta_rows  train  validation  sealed_holdout  train_validation_available  numeric_features        integrity
    SOCCER              698366         686587      -11779 384820      154497          147270                      539317                59 PASS_AS_REPORTED
       NFL                1424           7381        5957   5395        1019             967                        6414                59 PASS_AS_REPORTED
       NBA               29627          29627           0  20783        4389            4455                       25172                59 PASS_AS_REPORTED
  NCAA_MEN                6292         201162      194870 135924       32854           32384                      168778                59 PASS_AS_REPORTED
NCAA_WOMEN                5634         144224      138590 100649       21467           22108                      122116                59 PASS_AS_REPORTED
      WNBA                 312           3990        3678   2734         604             652                        3338                59 PASS_AS_REPORTED
     TOTAL              741655        1072971      331316 650305      214830          207836                      865135                59 PASS_AS_REPORTED