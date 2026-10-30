import os,json,hashlib,warnings
from pathlib import Path
import numpy as np
import pandas as pd
import joblib

def digest(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

class ModelRuntime:
 def __init__(self,records,approved):
  self.models={r['model_id']:r for r in records}
  self.approved=set(approved)
  self.cache={}
 def model_record(self,mid):
  if mid not in self.models:raise KeyError('MODEL_NOT_REGISTERED')
  if mid not in self.approved:raise PermissionError('MODEL_NOT_ACTIVATED')
  m=self.models[mid]
  if not m.get('artifact'):raise RuntimeError('MODEL_ARTIFACT_UNAVAILABLE')
  return m
 def _bundle(self,m):
  mid=m['model_id']
  if mid not in self.cache:
   path=Path(m['artifact'])
   if not path.is_file() or digest(path)!=m['sha256']:
    raise RuntimeError('MODEL_ARTIFACT_HASH_MISMATCH')
   with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    self.cache[mid]=joblib.load(path)
  return self.cache[mid]
 def predict(self,mid,record):
  m=self.model_record(mid)
  if not isinstance(record,dict):raise ValueError('FEATURES_MUST_BE_OBJECT')
  bundle=self._bundle(m)
  if isinstance(bundle,dict) and isinstance(bundle.get('fitted'),dict):
   fitted=bundle['fitted']; model=fitted.get('model');imp=fitted.get('imputer');sel=fitted.get('indices')
   names=bundle.get('feature_columns') or bundle.get('native_59_feature_names')
   if names is None or imp is None or sel is None:raise RuntimeError('ORIGINAL_WRAPPER_INCOMPLETE')
   names=list(map(str,names))
   if len(names)!=len(set(names)):raise RuntimeError('DUPLICATE_ORIGINAL_FEATURES')
   missing=[x for x in names if x not in record]
   extra=[x for x in record if x not in names]
   if missing or extra:raise ValueError('NATIVE_FEATURE_CONTRACT_MISMATCH missing='+str(missing[:8])+' extra='+str(extra[:8]))
   x=pd.DataFrame([[record[n] for n in names]],columns=names)
   x=x.apply(pd.to_numeric,errors='raise')
   if np.isinf(x.to_numpy(dtype=float)).any():raise ValueError('INFINITE_FEATURE')
   selected=np.asarray(sel)
   if selected.dtype.kind=='b':
    if len(selected)!=len(names):raise ValueError('MASK_SIZE_MISMATCH')
    selected=np.flatnonzero(selected)
   elif selected.dtype.kind in ('i','u'): selected=selected.astype(int)
   else:raise ValueError('INVALID_INDICES')
   if selected.ndim!=1 or len(selected)==0 or len(set(selected.tolist()))!=len(selected) or (selected<0).any() or (selected>=len(names)).any():raise ValueError('INVALID_SELECTED_INDICES')
   # Preserve trained preprocessing ordering: selected columns before
   # imputation if imputer expects selected width, otherwise full width.
   fitted_n=getattr(imp,'n_features_in_',None)
   if fitted_n==len(selected):
    transformed=imp.transform(x.iloc[:,selected])
   elif fitted_n==len(names):
    transformed=imp.transform(x)
    transformed=transformed[:,selected]
   else:raise RuntimeError('IMPUTER_INPUT_WIDTH_UNVERIFIED')
  else:
   model=bundle
   names=m.get('feature_order')
   if not names:raise RuntimeError('ORIGINAL_FEATURE_ORDER_UNAVAILABLE')
   missing=[n for n in names if n not in record];extra=[n for n in record if n not in names]
   if missing or extra:raise ValueError('NATIVE_FEATURE_CONTRACT_MISMATCH missing='+str(missing[:8])+' extra='+str(extra[:8]))
   transformed=pd.DataFrame([[record[n] for n in names]],columns=names)
  if not callable(getattr(model,'predict',None)):raise RuntimeError('MODEL_PREDICT_UNAVAILABLE')
  labels=model.predict(transformed)
  scalar=np.asarray(labels).reshape(-1)[0]
  if hasattr(scalar,'item'):scalar=scalar.item()
  out={'model_id':mid,'prediction':scalar,'original_sha256':m['sha256'],'feature_count':len(record)}
  if callable(getattr(model,'predict_proba',None)):
   probs=np.asarray(model.predict_proba(transformed),dtype=float)
   if probs.ndim!=2 or probs.shape[0]!=1 or not np.isfinite(probs).all():raise RuntimeError('INVALID_PROBABILITIES')
   out['probabilities']=probs[0].tolist()
   classes=getattr(model,'classes_',None)
   if classes is not None:out['classes']=[x.item() if hasattr(x,'item') else x for x in classes]
  return out
