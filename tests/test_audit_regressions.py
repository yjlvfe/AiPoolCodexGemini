"""Behavioral regressions from direct source review; no production credentials."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
ROOT = Path(__file__).resolve().parents[1]
for d in ('bridges','cli','dashboard'):
    sys.path.insert(0, str(ROOT/d))
import gemini_bridge as gemini
import codex_bridge as codex
from pool_runtime import PoolError
from durable_requests import request_key
from app import TokenAuthManager


def payload(messages=None, **extra):
    return {'model':'gemini-3.8-flash', 'messages': messages or [{'role':'user','content':'hello'}], **extra}


def test_tool_schema_description_and_duplicates_are_not_rewritten():
    schema={'type':'object','properties':{'type':{'type':'string'},'value':{'anyOf':[{'type':'integer','minimum':3},{'type':'null'}]}},'additionalProperties':False,'required':['type'], '$defs':{'x':{'type':'string'}}}
    tool={'type':'function','function':{'name':'inspect','description':'first\n\n'+'details '*90,'parameters':schema}}
    source=payload(tools=[tool,copy.deepcopy(tool)])
    result=gemini.to_antigravity_body(source)['request']['tools'][0]['functionDeclarations']
    assert len(result)==2
    assert result[0]['description']==tool['function']['description']
    assert result[0]['parametersJsonSchema']==schema


def test_parts_whitespace_and_top_level_instructions_preserved():
    source=payload([{'role':'system','content':[{'type':'text','text':'system\n\n'}]}, {'role':'user','content':'  \n'}, {'role':'assistant','content':[{'type':'text','text':'answer'}]}, {'role':'user','content':'end'}],instructions='top-level')
    out=gemini.to_antigravity_body(source)['request']
    assert [p['text'] for p in out['systemInstruction']['parts']]==['top-level','system\n\n']
    assert out['contents'][0]['parts'][0]['text']=='  \n'
    assert out['contents'][1]['parts'][0]['text']=='answer'


@pytest.mark.parametrize('message', [{'role':'alien','content':'keep'}, {'role':'user','content':[{'type':'audio','data':'keep'}]}])
def test_unsupported_content_rejected_not_dropped(message):
    with pytest.raises(PoolError) as e: gemini.to_antigravity_body(payload([message]))
    assert e.value.status==400


def test_image_url_is_forwarded():
    out=gemini.to_antigravity_body(payload([{'role':'user','content':[{'type':'image_url','image_url':{'url':'https://example.org/image.png'}}]}]))
    assert out['request']['contents'][0]['parts'][0]['fileData']['fileUri']=='https://example.org/image.png'


def test_codex_chat_instructions_are_not_overwritten():
    out=codex.response_request(payload(instructions='top-level'),chat=True)
    assert 'top-level' in out['instructions']


def test_output_length_finish_reason():
    out=gemini.to_openai_response({'candidates':[{'content':{'parts':[{'text':'partial'}]},'finishReason':'MAX_TOKENS'}]})
    assert out['choices'][0]['finish_reason']=='length'


def test_blocked_provider_response_is_not_fake_success():
    with pytest.raises(PoolError):
        gemini.to_openai_response({'promptFeedback':{'blockReason':'SAFETY'}})


def test_magic_link_expires_after_30_minutes(tmp_path,monkeypatch):
    manager=TokenAuthManager(str(tmp_path/'auth.db'))
    now=time.time()
    monkeypatch.setattr(time,'time',lambda:now)
    link=manager.generate_magic_link('http://localhost',1)
    monkeypatch.setattr(time,'time',lambda:now+1801)
    assert manager.register_device(link.split('token=')[1],'127.0.0.1','test') is None


def test_anonymous_requests_are_not_implicitly_cached():
    assert request_key('Gemini','/chat',{'x':1}) != request_key('Gemini','/chat',{'x':1})


def test_explicit_keys_are_client_scoped():
    assert request_key('Gemini','/chat',{},'same',client_scope='a') != request_key('Gemini','/chat',{},'same',client_scope='b')


def test_nested_account_lock_does_not_deadlock(tmp_path):
    code="from account_manager import Manager\nm=Manager('codex')\nwith m.locked():\n with m.locked():\n  print('nested-ok')\n"
    result=subprocess.run([sys.executable,'-c',code],env={**os.environ,'PYTHONPATH':str(ROOT/'cli'),'CODEX_ACCOUNT_STORE':str(tmp_path)},capture_output=True,text=True,timeout=2)
    assert result.returncode==0 and 'nested-ok' in result.stdout


def test_bot_denies_unlisted_user(monkeypatch):
    import bot_service as bot
    monkeypatch.setenv('AIPOOL_ADMIN_USER_IDS','123')
    sent=[]
    monkeypatch.setattr(bot,'call_tg',lambda *a,**k:sent.append(a))
    monkeypatch.setattr(bot.auth_manager,'generate_magic_link',lambda *a:pytest.fail('unauthorized link minted'))
    bot.send_dashboard_link(999,999)
    assert not sent
