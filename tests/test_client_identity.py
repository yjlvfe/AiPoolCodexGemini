import sys,json,tempfile,hashlib,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bridges'))
from client_identity import identify
class IdentityTests(unittest.TestCase):
 def test_identity_and_denial(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'clients.json'; cfg={'mode':'enforce','clients':[{'name':'Hermes','id':'test','enabled':True,'sha256':hashlib.sha256(b'synthetic').hexdigest()}]};p.write_text(json.dumps(cfg))
   self.assertTrue(identify({'Authorization':'Bearer synthetic'},p)['identity_verified'])
   self.assertFalse(identify({'X-AI-Client':'Hermes'},p)['allowed'])
   self.assertFalse(identify({'Authorization':'Bearer wrong'},p)['allowed'])
   cfg['clients'][0]['enabled']=False;p.write_text(json.dumps(cfg))
   self.assertFalse(identify({'Authorization':'Bearer synthetic'},p)['allowed'])
 def test_missing_registry(self):
  with self.assertRaises(OSError):identify({},'/nonexistent/aipool-registry.json')
 def test_loopback_trust(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'clients.json';cfg={'mode':'enforce','clients':[]};p.write_text(json.dumps(cfg))
   ident=identify({},p,peer_ip='127.0.0.1')
   self.assertTrue(ident['allowed']);self.assertFalse(ident['identity_verified'])
   self.assertFalse(identify({},p,peer_ip='198.51.100.2')['allowed'])
if __name__=='__main__':unittest.main()
