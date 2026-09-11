import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from codex_bridge import response_request
from pool_runtime import PoolError


class ChatToResponsesMappingTests(unittest.TestCase):
    def test_system_and_user_ordering(self):
        payload = {
            'model': 'gpt-5.6-luna',
            'messages': [
                {'role': 'system', 'content': 'Be concise'},
                {'role': 'user', 'content': 'hi'},
            ],
        }
        out = response_request(payload, chat=True)
        self.assertEqual(out['instructions'], 'Be concise')
        self.assertEqual(out['input'][0]['role'], 'user')

    def test_tool_call_round_trip(self):
        payload = {
            'model': 'gpt-5.6-luna',
            'messages': [
                {'role': 'user', 'content': 'hi'},
                {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'call_1', 'type': 'function',
                     'function': {'name': 'read_file', 'arguments': {'path': '/x'}}},
                ]},
                {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'file content'},
            ],
        }
        out = response_request(payload, chat=True)
        self.assertEqual(out['input'][0]['role'], 'user')
        self.assertEqual(out['input'][1]['type'], 'function_call')
        self.assertEqual(out['input'][1]['call_id'], 'call_1')
        self.assertEqual(json.loads(out['input'][1]['arguments']), {'path': '/x'})
        self.assertEqual(out['input'][2]['type'], 'function_call_output')
        self.assertEqual(out['input'][2]['call_id'], 'call_1')

    def test_missing_model_rejected(self):
        with self.assertRaises(PoolError):
            response_request({'messages': [{'role': 'user', 'content': 'x'}]}, chat=True)

    def test_tool_message_without_call_id_rejected(self):
        payload = {'model': 'gpt-5.6-luna', 'messages': [{'role': 'tool', 'content': 'out'}]}
        with self.assertRaises(PoolError) as ctx:
            response_request(payload, chat=True)
        self.assertIn('tool_call_id', str(ctx.exception))

    def test_function_call_without_id_rejected(self):
        payload = {
            'model': 'gpt-5.6-luna',
            'messages': [
                {'role': 'assistant', 'tool_calls': [
                    {'type': 'function', 'function': {'name': 'x', 'arguments': '{}'}},
                ]},
            ],
        }
        with self.assertRaises(PoolError):
            response_request(payload, chat=True)

    def test_tool_choice_conversion(self):
        payload = {
            'model': 'gpt-5.6-luna',
            'messages': [{'role': 'user', 'content': 'hi'}],
            'tools': [{'type': 'function', 'function': {'name': 'x'}}],
            'tool_choice': {'type': 'function', 'function': {'name': 'x'}},
        }
        out = response_request(payload, chat=True)
        self.assertEqual(out['tool_choice'], {'type': 'function', 'name': 'x'})

    def test_sampling_fields_removed_from_upstream(self):
        payload = {
            'model': 'gpt-5.6-luna',
            'messages': [{'role': 'user', 'content': 'hi'}],
            'temperature': 0.7,
            'max_tokens': 100,
            'n': 2,
        }
        out = response_request(payload, chat=True)
        for key in ('temperature', 'max_tokens', 'max_completion_tokens', 'n'):
            self.assertNotIn(key, out)

    def test_native_responses_input_does_not_receive_chat_messages(self):
        out = response_request({
            'model': 'gpt-5.6-luna',
            'input': [{'role': 'user', 'content': 'hi'}],
        }, chat=False)
        self.assertNotIn('messages', out)
        self.assertEqual(out['input'][0]['content'], 'hi')
    def test_responses_instructions_list_is_provider_text(self):
        out = response_request({
            'model': 'gpt-5.6-luna',
            'instructions': ['first', 'second'],
            'input': 'hello',
        }, chat=False)
        self.assertEqual(out['instructions'], 'first\n\nsecond')


if __name__ == '__main__':
    unittest.main()
