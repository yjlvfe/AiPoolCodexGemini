"""Read optional local configuration without executing shell commands."""
import os
from pathlib import Path
import shlex


def load():
    path = Path(os.environ.get('AIPOOL_CONFIG_ENV', Path(__file__).resolve().parents[1] / 'config.env'))
    if not path.is_file():
        return
    for number, raw in enumerate(path.read_text().splitlines(),1):
        line=raw.strip()
        if not line or line.startswith('#'):continue
        if line.startswith('export '):line=line[7:]
        key, sep, value=line.partition('=')
        if not sep or not key.replace('_','a').isalnum():
            raise ValueError(f'Invalid config.env line {number}')
        parts=shlex.split(value,comments=True)
        if len(parts)>1:raise ValueError(f'Quote spaces in config.env line {number}')
        os.environ.setdefault(key,parts[0] if parts else '')
