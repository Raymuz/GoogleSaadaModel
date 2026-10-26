# GOOGLESAADA — IMPORT COMBINED MODEL PACKAGE ONCE; PERSIST TO DRIVE
# Safe archive extraction; only writes under chosen destination; NEVER loads joblib.
from pathlib import Path
import zipfile, hashlib, json, shutil
from google.colab import drive, files

drive.mount('/content/drive')
DRIVE_DEST = Path('/content/drive/MyDrive/GoogleSaada_V9/model_registry/adaptive_19_model_source_kit')
DRIVE_DEST.mkdir(parents=True, exist_ok=True)
print('Upload GoogleSaada_V9_Adaptive_19_Model_OneUpload_Kit.zip')
uploaded=files.upload()
if len(uploaded)!=1 or not next(iter(uploaded)).lower().endswith('.zip'):
    raise ValueError('Upload only the combined .zip file')
archive=Path('/content')/next(iter(uploaded))
if not archive.is_file():
    archive.write_bytes(next(iter(uploaded.values())))
with zipfile.ZipFile(archive) as z:
    if z.testzip(): raise RuntimeError('ZIP CRC check failed')
    names=z.namelist()
    expected=z.read('manifest.json')
    manifest=json.loads(expected)
    for entry in manifest['files']:
        member=entry['path']
        pure=__import__('pathlib').PurePosixPath(member)
        if member.startswith('/') or '..' in pure.parts or '\\' in member:
            raise RuntimeError('Unsafe archive path '+member)
        digest=hashlib.sha256(z.read(member)).hexdigest()
        if digest!=entry['sha256']:
            raise RuntimeError('Model/package hash mismatch: '+member)
        target=DRIVE_DEST.joinpath(*pure.parts)
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==digest:
            continue
        temp=target.with_name(target.name+'.tmp')
        with z.open(member) as src,open(temp,'wb') as dst:
            shutil.copyfileobj(src,dst)
        temp.replace(target)
    (DRIVE_DEST/'manifest.json').write_bytes(expected)
print('IMPORT VERIFIED; NO MODEL DESERIALIZED')
print('PERSISTENT MODEL ROOT:',DRIVE_DEST)
print('MODEL BINARIES:',manifest['model_count'])
print('For future stages, set MODEL_REGISTRY_ROOT =',repr(str(DRIVE_DEST)))
