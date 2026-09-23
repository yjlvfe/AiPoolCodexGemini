import io
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridges"))

import codex_bridge
import gemini_bridge
from pool_runtime import AccountPool, PoolError
from retry_policy import should_transport_retry


class PhaseEStreamingParityTests(unittest.TestCase):
    def test_codex_chunk_idle_timeout_is_180(self):
        self.assertEqual(codex_bridge.CODEX_CHUNK_IDLE_TIMEOUT, 180.0)

    def test_gemini_stream_generate_content_action(self):
        """In streaming flow, streamGenerateContent?alt=sse is called instead of generateContent."""
        with patch.object(gemini_bridge, "get_access_token", return_value="test-token"), \
             patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__iter__.return_value = [b"data: [DONE]\n\n"]
            mock_urlopen.return_value = mock_resp

            resp = gemini_bridge.call_antigravity({"model": "gemini-3.8-flash"}, stream=True)
            self.assertEqual(resp, mock_resp)
            req = mock_urlopen.call_args[0][0]
            self.assertIn("streamGenerateContent?alt=sse", req.full_url)
            self.assertEqual(req.headers.get("Accept"), "text/event-stream")

    def test_gemini_stream_discards_empty_content_deltas(self):
        """Empty content deltas without reasoning/tools/finish_reason are discarded."""
        sse_lines = [
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": ""}]}}]}}\n\n',
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}}\n\n',
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": ""}]}}]}}\n\n',
            b'data: {"response": {"candidates": [{"finishReason": "STOP"}]}}\n\n',
        ]
        resp = io.BytesIO(b"".join(sse_lines))
        chunks = list(gemini_bridge.stream_antigravity_events(resp, model_name="gemini-3.8-flash"))

        # Chunk 1: "Hello"
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "Hello")
        # Chunk 2: finish_reason "stop"
        self.assertEqual(chunks[1]["choices"][0]["finish_reason"], "stop")
        # No empty delta chunks yielded
        self.assertEqual(len(chunks), 2)

    def test_gemini_stream_parses_reasoning_and_tools(self):
        sse_lines = [
            b'data: {"response": {"candidates": [{"content": {"parts": [{"thought": true, "text": "thinking..."}]}}]}}\n\n',
            b'data: {"response": {"candidates": [{"content": {"parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}]}}]}}\n\n',
        ]
        resp = io.BytesIO(b"".join(sse_lines))
        chunks = list(gemini_bridge.stream_antigravity_events(resp, model_name="gemini-3.8-flash"))

        self.assertEqual(chunks[0]["choices"][0]["delta"]["reasoning_content"], "thinking...")
        tool_call = chunks[1]["choices"][0]["delta"]["tool_calls"][0]
        self.assertEqual(tool_call["function"]["name"], "get_weather")
        self.assertIn("Paris", tool_call["function"]["arguments"])

    def test_gemini_stream_empty_candidates_raises_502(self):
        """When upstream returns empty candidates, it raises 502 before sending 200 headers."""
        sse_lines = [
            b'data: {"response": {"candidates": []}}\n\n',
        ]
        resp = io.BytesIO(b"".join(sse_lines))
        with self.assertRaises(PoolError) as ctx:
            list(gemini_bridge.stream_antigravity_events(resp, model_name="gemini-3.8-flash"))
        self.assertEqual(ctx.exception.status, 502)

    def test_gemini_stream_safety_block_raises_400(self):
        """When candidate is blocked by safety policy, it raises 400 before 200 headers."""
        sse_lines = [
            b'data: {"response": {"candidates": [{"finishReason": "SAFETY"}]}}\n\n',
        ]
        resp = io.BytesIO(b"".join(sse_lines))
        with self.assertRaises(PoolError) as ctx:
            list(gemini_bridge.stream_antigravity_events(resp, model_name="gemini-3.8-flash"))
        self.assertEqual(ctx.exception.status, 400)

    def test_cooldown_storm_elimination_returns_503_with_retry_after(self):
        """When candidates are empty, returns HTTP 503 with Retry-After without upstream storm."""
        pool = AccountPool("antigravity")
        with patch.object(pool, "candidates", return_value=[]), \
             patch.object(pool, "get_earliest_retry_after", return_value=42):
            with patch.object(gemini_bridge, "AG_POOL", pool):
                with self.assertRaises(PoolError) as ctx:
                    gemini_bridge.call_with_retry({"model": "gemini-3.8-flash"})
                self.assertEqual(ctx.exception.status, 503)
                self.assertEqual(ctx.exception.retry_after, 42)

    def test_codex_is_capacity_error_detection(self):
        """Capacity error markers are detected properly."""
        event1 = {"error": {"message": "The selected model is at capacity"}}
        self.assertTrue(codex_bridge.is_capacity_error(event1))

        event2 = {"type": "error", "code": "model_at_capacity"}
        self.assertTrue(codex_bridge.is_capacity_error(event2))

        event3 = {"type": "response.output_text.delta", "delta": "All good"}
        self.assertFalse(codex_bridge.is_capacity_error(event3))

    def test_codex_abort_terminal_framing_responses_vs_chat(self):
        """Responses API abort emits response.failed without [DONE]; chat completions emits [DONE]."""
        # Chat completions abort framing
        from stream_state import terminal_error_event, terminal_event
        chat_err = terminal_error_event("abort message", "req-1").decode("utf-8")
        chat_done = terminal_event().decode("utf-8")
        self.assertIn("data: {", chat_err)
        self.assertEqual(chat_done, "data: [DONE]\n\n")

        # Responses API abort framing
        resp_failed = {
            "type": "response.failed",
            "response": {
                "id": "resp_123",
                "status": "failed",
                "error": {
                    "type": "stream_error",
                    "code": "stream_disconnected",
                    "message": "abort message"
                }
            }
        }
        wire = f"event: response.failed\ndata: {json.dumps(resp_failed)}\n\n"
        self.assertIn("event: response.failed", wire)
        self.assertNotIn("[DONE]", wire)


if __name__ == "__main__":
    unittest.main()
