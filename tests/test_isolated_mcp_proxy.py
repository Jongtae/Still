import base64
import json
import unittest

from personal_agent.isolated_mcp_proxy import IsolatedMcpProxy, TaskCapabilityRegistry


class _FakeTools:
    def __init__(self, *, failure=None):
        self.calls = []
        self.failure = failure

    def definitions(self):
        # The proxy must not trust a facade catalogue containing broad tools.
        return [{"name": "save_note"}, {"name": "web_search"}, {"name": "shell"}]

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if self.failure:
            raise self.failure
        return {"notes": [{"id": "n1", "content": "approved note"}]}


def _call(name="list_notes", arguments=None, ident=1):
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": ident,
            "method": "tools/call",
            "params": {"name": name, "arguments": {} if arguments is None else arguments},
        }
    )


class IsolatedMcpProxyTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.registry = TaskCapabilityRegistry(clock=lambda: self.now[0])
        self.proxy = IsolatedMcpProxy(self.registry)

    def test_256_bit_task_token_allows_exactly_one_read_only_call(self):
        tools = _FakeTools()
        token = self.registry.register("task-1", tools, ttl_seconds=5)
        padding = "=" * (-len(token) % 4)
        self.assertEqual(len(base64.urlsafe_b64decode(token + padding)), 32)

        response = self.proxy.handle(_call(), token=token, task_id="task-1")

        self.assertEqual(response["jsonrpc"], "2.0")
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["notes"][0]["content"], "approved note")
        self.assertEqual(tools.calls, [("list_notes", {})])
        replay = self.proxy.handle(_call(ident=2), token=token, task_id="task-1")
        self.assertEqual(replay["error"]["code"], -32003)
        self.assertEqual(tools.calls, [("list_notes", {})])

    def test_missing_bad_foreign_and_expired_tokens_fail_without_invocation(self):
        tools = _FakeTools()
        missing = self.proxy.handle(_call(), token=None, task_id="task-1")
        bad = self.proxy.handle(_call(), token="invented", task_id="task-1")
        self.assertEqual(missing["error"]["code"], -32001)
        self.assertEqual(bad["error"]["code"], -32001)

        foreign_token = self.registry.register("task-1", tools)
        foreign = self.proxy.handle(_call(), token=foreign_token, task_id="task-2")
        self.assertEqual(foreign["error"]["code"], -32001)
        replay = self.proxy.handle(_call(), token=foreign_token, task_id="task-1")
        self.assertEqual(replay["error"]["code"], -32003)

        expired_token = self.registry.register("task-1", tools, ttl_seconds=1)
        self.now[0] += 1
        expired = self.proxy.handle(_call(), token=expired_token, task_id="task-1")
        self.assertEqual(expired["error"]["code"], -32002)
        self.assertEqual(tools.calls, [])

    def test_unknown_or_mutating_tools_never_reach_broad_facade(self):
        for name, arguments in (
            ("save_note", {"content": "write"}),
            ("web_search", {"query": "private"}),
            ("shell", {"command": "id"}),
            ("list_notes", {"unexpected": True}),
        ):
            with self.subTest(name=name):
                tools = _FakeTools()
                token = self.registry.register("task", tools)
                response = self.proxy.handle(_call(name, arguments), token=token, task_id="task")
                self.assertEqual(response["error"]["code"], -32601)
                self.assertEqual(tools.calls, [])

    def test_malformed_json_and_facade_errors_are_sanitized(self):
        tools = _FakeTools(failure=RuntimeError("secret owner path /private/state"))
        token = self.registry.register("task", tools)
        malformed = self.proxy.handle('{"jsonrpc":"2.0",', token=token, task_id="task")
        self.assertEqual(malformed["error"]["code"], -32700)
        # Parsing failures do not consume a capability because no valid action
        # was presented; the subsequent valid call does consume it.
        failed = self.proxy.handle(_call(), token=token, task_id="task")
        rendered = json.dumps(failed)
        self.assertEqual(failed["error"]["code"], -32603)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("/private", rendered)
        self.assertEqual(tools.calls, [("list_notes", {})])

    def test_request_shape_is_exact_and_response_json_round_trips(self):
        tools = _FakeTools()
        token = self.registry.register("task", tools)
        request = json.loads(_call())
        request["extra"] = True
        rejected = self.proxy.handle(json.dumps(request), token=token, task_id="task")
        self.assertEqual(rejected["error"]["code"], -32600)
        self.assertEqual(tools.calls, [])

        response_text = self.proxy.handle_json(_call(), token=token, task_id="task")
        self.assertEqual(json.loads(response_text)["id"], 1)
        self.assertEqual(tools.calls, [("list_notes", {})])


if __name__ == "__main__":
    unittest.main()
