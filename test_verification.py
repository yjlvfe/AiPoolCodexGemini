import urllib.request
import os

def test_structure():
    base = "/root/Projects/ai-pool-suite"
    assert os.path.isdir(base)
    assert os.path.isfile(os.path.join(base, "install.sh"))
    assert os.path.isfile(os.path.join(base, "README.md"))
    assert os.path.isfile(os.path.join(base, "bridges/gemini_bridge.py"))
    assert os.path.isfile(os.path.join(base, "bridges/codex_bridge.py"))
    assert os.path.isfile(os.path.join(base, "dashboard/server.py"))
    assert os.path.isfile(os.path.join(base, "cli/ag"))
    assert os.path.isfile(os.path.join(base, "cli/cx"))
    print("STRUCTURE_OK")

def test_ports():
    with urllib.request.urlopen('http://127.0.0.1:8123/v1/models', timeout=3) as r:
        assert r.status == 200
    with urllib.request.urlopen('http://127.0.0.1:8124/v1/models', timeout=3) as r:
        assert r.status == 200
    with urllib.request.urlopen('http://127.0.0.1:8444/api/logs_data', timeout=3) as r:
        assert r.status == 200
    print("PORTS_OK")

if __name__ == "__main__":
    test_structure()
    test_ports()
