import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridges'))
import codex_bridge
import gemini_bridge


class ReasoningNormalizationTests(unittest.TestCase):
    def test_gemini_tiers(self):
        expected = {'low': 2000, 'medium': 8000, 'high': 16000,
                    'xhigh': 16000, 'max': 16000, 'ultra': 16000}
        for level, budget in expected.items():
            body = gemini_bridge.to_antigravity_body({
                'model': 'gemini-3.8-flash',
                'messages': [{'role': 'user', 'content': 'x'}],
                'reasoning_effort': level,
            })
            self.assertEqual(
                body['request']['generationConfig']['thinkingConfig']['thinkingBudget'],
                budget,
                level,
            )

    def test_codex_tiers(self):
        expected = {'none': 'low', 'minimal': 'low', 'low': 'low',
                    'medium': 'medium', 'high': 'high', 'xhigh': 'xhigh',
                    'max': 'max', 'ultra': 'ultra'}
        for level, normalized in expected.items():
            body = codex_bridge.response_request({
                'model': 'gpt-6-astra',
                'messages': [{'role': 'user', 'content': 'x'}],
                'reasoning_effort': level,
            })
            self.assertEqual(body['reasoning'], {'effort': normalized})
            self.assertNotIn('reasoning_effort', body)


if __name__ == '__main__':
    unittest.main()
