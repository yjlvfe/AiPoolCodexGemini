import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
from stream_state import StreamLifecycle, StreamState
from tool_registry import ToolRegistry

class StreamToolTests(unittest.TestCase):
    def test_stream_cannot_failover_after_output(self):
        s = StreamLifecycle()
        self.assertTrue(s.can_failover)
        s.begin(); s.observe()
        self.assertFalse(s.can_failover)
        s.interrupt()
        self.assertEqual(s.state, StreamState.INTERRUPTED)

    def test_conflicting_tool_name_is_rejected(self):
        r = ToolRegistry()
        first = {'type':'function','function':{'name':'search','parameters':{'type':'object','required':['q']}}}
        second = {'type':'function','function':{'name':'search','parameters':{'type':'object','required':['query']}}}
        r.register(first)
        with self.assertRaises(ValueError):
            r.register(second)

    def test_tool_schema_deduplication_preserves_schema(self):
        t = {'type':'function','function':{'name':'search','parameters':{'type':'object','required':['q']}}}
        r = ToolRegistry()
        self.assertEqual(r.deduplicate([t, t]), [t])
        self.assertEqual(r.resolve('search'), t)

    def test_claude_model_tool_schema_sanitization(self):
        from gemini_bridge import _build_tools
        tools = [{
            'type': 'function',
            'function': {
                'name': 'test_fn',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'query': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}
                    }
                }
            }
        }]
        res = _build_tools(tools, wire_model='claude-sonnet-4-6')
        decl = res[0]['functionDeclarations'][0]
        self.assertIn('parameters', decl)
        self.assertNotIn('parametersJsonSchema', decl)
        self.assertIn('oneOf', decl['parameters']['properties']['query'])
        self.assertNotIn('anyOf', decl['parameters']['properties']['query'])

if __name__ == '__main__':
    unittest.main()
