"""Launch cache-only Task D finalization without a controlling terminal."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from run_status import inventory
from stems import library_active

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
PYTHON='/home/usr/venvs/midi/bin/python'

def main():
 if sys.executable != PYTHON: raise RuntimeError('Use the required MIDI interpreter')
 if library_active(): raise RuntimeError('Library processing is active; no concurrent analysis started')
 if (HERE/'REPORT.md').exists(): raise RuntimeError('Task D report exists; inspect before running again')
 current=subprocess.run(['ps','-eo','args='],capture_output=True,text=True,check=True).stdout
 if any('python' in line and 'emotion/postprocess.py' in line for line in current.splitlines()):
  raise RuntimeError('Task D finalization is already active')
 status=inventory()
 if status['separation'].get('complete')!=325 or any(v['remaining_without_cache'] for v in status['stages'].values()):
  raise RuntimeError('Some score caches are incomplete; do not finalize')
 env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':os.pathsep.join([str(HERE),str(HERE/'vendor'),str(ROOT)])}
 with (HERE/'postprocess.log').open('a',encoding='utf-8') as output:
  output.write('\n'+json.dumps({'event':'start','utc':datetime.now(timezone.utc).isoformat()})+'\n');output.flush()
  process=subprocess.Popen([PYTHON,'-u','emotion/postprocess.py'],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                           stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
 state={'pid':process.pid,'started_utc':datetime.now(timezone.utc).isoformat(),'log':'emotion/postprocess.log'}
 (HERE/'validation'/'finalize_worker.json').write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(state))

if __name__=='__main__': main()
