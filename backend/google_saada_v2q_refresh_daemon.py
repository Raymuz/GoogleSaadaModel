"""V2R audited recurring evidence refresh."""
from datetime import datetime, timezone
from pathlib import Path
import json, os, subprocess, sys, threading, time

APP = Path(__file__).resolve().parent.parent
STAGE = APP / 'global_v3/v2g_staging'
SCRIPT = STAGE / 'gs_v2q_v2m_refresh.py'
RUNNER = APP / 'backend/google_saada_v2r_history_runner.py'
SCRIPT_SHA = 'd99c9d88d5fca0b4b65ec1cd9fe7857aa6deedb4f55d0082f6af8b5646038011'
_LOCK = threading.Lock()
_STARTED = False

def _fresh():
    reports = sorted(STAGE.glob('v2m_generalized_live_staging_*.json'),
                     key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    if not reports:
        return False
    try:
        data = json.loads(reports[0].read_text(encoding='utf-8'))
        stamp = datetime.fromisoformat(data['timestamp_utc'].replace('Z','+00:00'))
        return stamp.tzinfo is not None and not data.get('blockers') and 0 <= (datetime.now(timezone.utc)-stamp).total_seconds() < 75*60
    except (OSError, ValueError, KeyError, TypeError):
        return False

def _loop():
    while True:
        if not _fresh() and _LOCK.acquire(blocking=False):
            try:
                env = os.environ.copy()
                env['GS_V2R_COLLISION_AUDIT'] = str(STAGE/'v2r_live_history_collision_audit.json')
                env['GS_V2R_MAX_HISTORY_REVISIONS'] = '10'
                result = subprocess.run(
                    [sys.executable,str(RUNNER),str(SCRIPT),SCRIPT_SHA],
                    cwd=str(APP), env=env, capture_output=True,
                    text=True, timeout=1800, check=False
                )
                if result.returncode:
                    print('[GS V2R] Source refresh blocked:',
                          (result.stdout+'\n'+result.stderr)[-1200:],
                          flush=True)
                else:
                    print('[GS V2R] Audited V2M evidence refresh succeeded',flush=True)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print('[GS V2R] Source refresh failed:',
                      type(exc).__name__, str(exc)[:300], flush=True)
            finally:
                _LOCK.release()
        time.sleep(300)

def start():
    global _STARTED
    if os.environ.get('GS_V2Q_AUTO_REFRESH','1') != '1' or _STARTED:
        return
    _STARTED = True
    threading.Thread(
        target=_loop, daemon=True,
        name='gs-v2r-audited-refresh'
    ).start()
