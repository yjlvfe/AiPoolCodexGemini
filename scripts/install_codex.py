"""Install official Codex in the user's prefix without touching agent profiles."""
from pathlib import Path
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from config_env import load
load()
from account_manager import find_codex
try:
    try:
        binary=find_codex()
    except ValueError:
        npm=shutil.which('npm')
        if not npm:
            raise ValueError('Node.js/npm is required for Codex login. Install a supported Node.js LTS from nodejs.org; Antigravity login needs no Node.js.')
        prefix=Path.home()/'.local'
        subprocess.run([npm,'install','--global','--prefix',str(prefix),'@openai/codex'],check=True,timeout=240)
        binary=find_codex()
    subprocess.run([binary,'--version'],check=True,timeout=20)
    print('Codex CLI verified. Antigravity OAuth is built into this suite.')
except (ValueError,OSError,subprocess.SubprocessError) as exc:
    print(str(exc),file=sys.stderr)
    raise SystemExit(1)
