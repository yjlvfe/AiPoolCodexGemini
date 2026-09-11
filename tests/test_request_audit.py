import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
import request_audit as audit
import request_log as log


class Handler:
    client_address = ('127.0.0.1', 123)
    path = '/v1/responses?secret=hidden'
    headers = {
        'Authorization': 'Bearer NEVER_STORE',
        'X-Forwarded-For': 'spoofed',
        'X-AI-Client': 'test-client',
    }


class AuditTests(unittest.TestCase):
    def test_capture(self):
        a = audit.capture(Handler(), {'input': 'hello', 'api_key': 'secret'})
        b = audit.capture(Handler(), {'input': 'hello', 'api_key': 'secret'})
        self.assertEqual(a['payload_sha256'], b['payload_sha256'])
        self.assertNotEqual(a['request_id'], b['request_id'])
        self.assertEqual(a['peer_ip'], '127.0.0.1')
        self.assertNotIn('secret', a['endpoint'])
        self.assertNotIn('NEVER_STORE', str(a))

    def test_redaction_bound(self):
        a = audit.capture(Handler(), {'messages': [{'content': 'Bearer abc «redacted:sk-…» api_key=abc'}]})
        self.assertNotIn('abc', a['prompt_text'])
        self.assertTrue(audit.capture(Handler(), {'input': 'x' * 20000})['prompt_capture_truncated'])

    def test_prompt_views_keep_client_and_final_provider_payloads_separate(self):
        raw = {
            'instructions': 'client system instruction',
            'messages': [{'role': 'user', 'content': 'client question'}],
        }
        provider = {
            'model': 'gemini-3.8-flash',
            'request': {
                'systemInstruction': {'parts': [{'text': 'provider system instruction'}]},
                'contents': [{'role': 'user', 'parts': [{'text': 'provider question'}]}],
            },
        }
        audit_record = audit.capture(Handler(), raw, raw)
        audit.attach_provider_payload(audit_record, provider)
        self.assertIn('client system instruction', audit_record['prompt_text'])
        self.assertIn('client question', audit_record['prompt_text'])
        self.assertIn('provider system instruction', audit_record['provider_prompt_text'])
        self.assertIn('provider question', audit_record['provider_prompt_text'])
        self.assertNotIn('client question', audit_record['provider_prompt_text'])

    def test_format_human_prompt_does_not_drop_instructions_with_messages(self):
        rendered = audit.format_human_prompt({
            'instructions': 'system text',
            'messages': [{'role': 'user', 'content': 'user text'}],
        })
        self.assertLess(rendered.index('system text'), rendered.index('user text'))

    def test_in_and_out_are_distinct_after_provider_adaptation(self):
        class Handler:
            client_address = ('127.0.0.1', 1)
            path = '/v1/chat/completions'
            headers = {'User-Agent': 'test'}

        incoming = {
            'model': 'gemini-3.8-flash',
            'messages': [{'role': 'user', 'content': 'hello'}],
            'stream': False,
        }
        outgoing = {
            'model': 'gemini-3.8-flash-tiered',
            'request': {
                'contents': [{'role': 'user', 'parts': [{'text': 'hello'}]}],
            },
        }
        record = audit.capture(Handler(), incoming)
        audit.attach_provider_payload(record, outgoing)
        self.assertNotEqual(record['prompt_text'], record['provider_prompt_text'])
        self.assertIn('IN PAYLOAD (EXACT JSON)', record['prompt_text'])
        self.assertIn('OUT PAYLOAD (EXACT JSON)', record['provider_prompt_text'])
        self.assertIn('messages', record['prompt_text'])
        self.assertIn('contents', record['provider_prompt_text'])

    def test_response_is_separate_from_provider_request_payload(self):
        record = {'prompt_text': 'IN request'}
        audit.attach_response(record, {
            'choices': [{'message': {'role': 'assistant', 'content': 'OUT answer'}}]
        })
        self.assertEqual(record['response_text'], 'OUT answer')
        self.assertNotEqual(record['prompt_text'], record['response_text'])

    def test_inspector_uses_v200_independent_prompt_boxes_and_provider_payload(self):
        template = Path(__file__).resolve().parents[1] / 'dashboard' / 'templates' / 'dashboard.html'
        html = template.read_text()
        self.assertIn('04 · IN Messages (Received from Agent)', html)
        self.assertIn('05 · OUT Messages (Provider Response)', html)
        self.assertEqual(html.count('createPromptBox('), 3)
        self.assertEqual(html.count("document.createElement('textarea')"), 1)
        self.assertIn('promptBox.readOnly = true', html)
        self.assertIn('promptBox.value = currentText || emptyMessage', html)
        self.assertIn('const formatQuotaPct = (value)', html)
        self.assertIn(": 'N/A';", html)
        self.assertIn('const statusBadge = accountStatus', html)
        self.assertIn('Number.isFinite(value)', html)
        self.assertNotIn("acc['5h_pct'] !== undefined ? acc['5h_pct'] + '%'", html)
        self.assertNotIn("acc['wk_pct'] !== undefined ? acc['wk_pct'] + '%'", html)
        self.assertIn('/api/request_prompt?id=', html)
        self.assertIn('data.provider_prompt_text', html)
        self.assertNotIn('Copy Active', html)
        self.assertNotIn('currentTab', html)
        self.assertNotIn('btnProcessed', html)
        self.assertNotIn('btnOriginal', html)
        self.assertIn('let accountsVisible = false;', html)
        self.assertIn('grid-template-columns: repeat(3, minmax(0, 1fr))', html)
        self.assertIn('function formatPoolNumber(value)', html)
        self.assertIn('if (abs >= 1e12)', html)
        self.assertIn('if (abs >= 1e9)', html)
        self.assertIn('if (abs >= 1e6)', html)
        self.assertIn("${sign}${trim(abs / 1e12)}T", html)
        self.assertIn("${sign}${trim(abs / 1e9)}B", html)
        self.assertIn("${sign}${trim(abs / 1e6)}M", html)
        self.assertIn("element.title = Number(value).toLocaleString('en-US')", html)
        self.assertNotIn("text-overflow: ellipsis;\n            transition: font-size 120ms ease;", html)
        self.assertNotIn("formatCompactNumber", html)
        self.assertNotIn("2x2 Clean Grid", html)
        self.assertIn('class="accounts-strip collapsed" id="pool-accounts-container"', html)
        self.assertNotIn('async function inspectRequest(', html)
        self.assertNotIn('copyInspectorPrompt(', html)

    def test_pool_number_format_has_no_thousand_abbreviation(self):
        template = Path(__file__).resolve().parents[1] / 'dashboard' / 'templates' / 'dashboard.html'
        html = template.read_text()
        start = html.index('        function formatPoolNumber(value) {')
        end = html.index('        function updateProviderStatsAndModels() {', start)
        function_source = html[start:end]
        script = function_source + "\nconsole.log(JSON.stringify([formatPoolNumber(999), formatPoolNumber(999999), formatPoolNumber(1000000), formatPoolNumber(1234567890), formatPoolNumber(1234567890123)]));"
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), ['999', '999,999', '1M', '1.23B', '1.23T'])
        self.assertNotIn('...', result.stdout)
        self.assertNotIn('k', result.stdout)

    def test_sqlite_migration_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {'AUTH_DB_PATH': d + '/test.db'}):
                a = audit.capture(Handler(), {'input': 'synthetic'})
                log.record('Codex', 'test-model', {'input_tokens': 2, 'output_tokens': 3}, audit=a)
                log.record('Codex', 'test-model', audit=a)
                report = log.report(d + '/test.db')
                self.assertEqual(len(report['recent_requests']), 1)
                self.assertEqual(report['recent_requests'][0]['audit'], {'prompt_capture_status': 'on_demand'})
                prompt = log.get_request_prompt(d + '/test.db', a['request_id'])
                self.assertEqual(prompt['prompt_text'], a['prompt_text'])
                self.assertEqual(report['total_pool_tokens'], 5)


if __name__ == '__main__':
    unittest.main()
