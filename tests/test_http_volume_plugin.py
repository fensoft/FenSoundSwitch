from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from plugin_api import RouteOutputDefinition, RouteOutputEditor, validate_plugin_ui_document
from plugins import http_volume_plugin as http_volume


PARAMETERS = {
    "read_url": "https://receiver.local/api/volume?zone=main",
    "read_method": "GET",
    "write_url": "http://receiver.local:8080/api/volume",
    "write_method": "PATCH",
    "headers": {"Authorization": "Bearer exported-value", "Content-Type": "application/json"},
    "body_template": '{"level":{volume}}',
    "response_field": "level",
}


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.read_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.payload


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, target: str, body: bytes | None, headers: dict[str, str]) -> None:
        self.requests.append((method, target, body, headers))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class HttpVolumePluginTests(unittest.TestCase):
    def test_api_v4_factory_editor_and_initialization_are_network_free(self) -> None:
        with patch.object(http_volume.http.client, "HTTPConnection") as http, patch.object(http_volume.http.client, "HTTPSConnection") as https:
            plugin = http_volume.create_plugin()
            plugin.initialize(Mock())
            output = plugin.create_output(PARAMETERS)
            document = validate_plugin_ui_document(plugin.get_route_output_ui(PARAMETERS))
            saved = plugin.invoke_ui_action("save", {
                **PARAMETERS,
                "headers": '{"Authorization":"Bearer exported-value","Content-Type":"application/json"}',
            })
        self.assertEqual(http_volume.PLUGIN_API_VERSION, 4)
        self.assertIsInstance(plugin, RouteOutputDefinition)
        self.assertIsInstance(plugin, RouteOutputEditor)
        self.assertFalse(output.supports_native_mute)
        self.assertEqual(output.is_volume_provider_available(), (True, None))
        self.assertIn("included in configuration exports", document["description"])
        self.assertEqual(saved["values"], PARAMETERS)
        http.assert_not_called()
        https.assert_not_called()

    def test_validation_rejects_invalid_urls_methods_headers_body_and_unknown_fields(self) -> None:
        changes = (
            {"read_url": "ftp://receiver/api"},
            {"read_url": "http://user:pass@receiver/api"},
            {"read_url": "http://receiver/api#fragment"},
            {"read_url": "http://receiver/api#"},
            {"read_method": "PUT"},
            {"write_method": "GET"},
            {"headers": {"Host": "evil.example"}},
            {"headers": {"Content-Length": "1"}},
            {"headers": {"Transfer-Encoding": "chunked"}},
            {"headers": {"Connection": "close"}},
            {"headers": {"Proxy-Authorization": "secret"}},
            {"headers": {"X-Test": "bad\r\nHost: evil"}},
            {"body_template": '{"level":1}'},
            {"body_template": '{"a":{volume},"b":{volume}}'},
            {"body_template": '{"level":{volume},}'},
            {"response_field": ""},
            {"extra": True},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                http_volume.validate_parameters({**PARAMETERS, **change})
        with self.assertRaises(ValueError):
            http_volume.validate_parameters({**PARAMETERS, "headers": {"X-Number": 1}})
        with self.assertRaises(ValueError):
            http_volume.HttpVolumePlugin().invoke_ui_action("save", {**PARAMETERS, "headers": '{"X":NaN}'})
        saved = http_volume.HttpVolumePlugin().invoke_ui_action("save", {**PARAMETERS, "headers": ""})
        self.assertEqual(saved["values"]["headers"], {})

    def test_limits_are_enforced(self) -> None:
        with self.assertRaises(ValueError):
            http_volume.validate_parameters({**PARAMETERS, "read_url": "http://receiver/" + "x" * http_volume.MAX_URL_LENGTH})
        with self.assertRaises(ValueError):
            http_volume.validate_parameters({**PARAMETERS, "headers": {"X-Large": "x" * http_volume.MAX_HEADERS_BYTES}})
        with self.assertRaises(ValueError):
            http_volume.validate_parameters({**PARAMETERS, "body_template": '{"value":"' + "x" * http_volume.MAX_BODY_BYTES + '{volume}"}'})

    def test_exact_read_and_confirmed_write_requests_use_timeout_and_bounded_reads(self) -> None:
        read_response = FakeResponse(b'{"level":41}')
        write_response = FakeResponse(b'{"accepted":true}', 204)
        confirm_response = FakeResponse(b'{"level":100}')
        read_connection = FakeConnection(read_response)
        write_connection = FakeConnection(write_response)
        confirm_connection = FakeConnection(confirm_response)
        output = http_volume.HttpVolumePlugin().create_output(PARAMETERS)
        with patch.object(http_volume.http.client, "HTTPSConnection", side_effect=[read_connection, confirm_connection]) as https, patch.object(http_volume.http.client, "HTTPConnection", return_value=write_connection) as http:
            self.assertEqual(output.read_volume(), 41)
            self.assertEqual(output.write_volume(999), 100)
        self.assertEqual(https.call_args_list[0].args, ("receiver.local", None))
        self.assertEqual(https.call_args_list[0].kwargs, {"timeout": 2.0})
        self.assertEqual(http.call_args.args, ("receiver.local", 8080))
        self.assertEqual(http.call_args.kwargs, {"timeout": 2.0})
        self.assertEqual(read_connection.requests, [("GET", "/api/volume?zone=main", None, PARAMETERS["headers"])])
        self.assertEqual(write_connection.requests, [("PATCH", "/api/volume", b'{"level":100}', PARAMETERS["headers"])])
        self.assertTrue(all(response.read_sizes == [http_volume.MAX_RESPONSE_BYTES + 1] for response in (read_response, write_response, confirm_response)))
        self.assertTrue(all(connection.closed for connection in (read_connection, write_connection, confirm_connection)))

    def test_oversized_status_and_redirect_responses_are_rejected_without_following(self) -> None:
        cases = (
            (FakeResponse(b"x" * (http_volume.MAX_RESPONSE_BYTES + 1)), "too large"),
            (FakeResponse(b"{}", 500), "status 500"),
            (FakeResponse(b"", 302), "redirect status 302"),
        )
        for response, message in cases:
            with self.subTest(message=message):
                connection = FakeConnection(response)
                output = http_volume.HttpVolumePlugin().create_output(PARAMETERS)
                with patch.object(http_volume.http.client, "HTTPSConnection", return_value=connection):
                    with self.assertRaisesRegex(http_volume.HttpVolumeError, message):
                        output.read_volume()
                self.assertEqual(response.read_sizes, [http_volume.MAX_RESPONSE_BYTES + 1])
                self.assertTrue(connection.closed)

    def test_malformed_non_object_nonfinite_and_invalid_volume_responses_are_rejected(self) -> None:
        cases = (b"not json", b"[]", b'{"level":NaN}', b'{"level":true}', b'{"level":1.5}', b'{"level":101}', b'{"other":20}', b'{"level":20,"level":21}')
        for payload in cases:
            with self.subTest(payload=payload):
                output = http_volume.HttpVolumePlugin().create_output(PARAMETERS)
                with patch.object(http_volume.http.client, "HTTPSConnection", return_value=FakeConnection(FakeResponse(payload))):
                    with self.assertRaises(http_volume.HttpVolumeError):
                        output.read_volume()

    def test_write_requires_exact_confirmed_readback(self) -> None:
        output = http_volume.HttpVolumePlugin().create_output(PARAMETERS)
        write = FakeConnection(FakeResponse(b"{}"))
        confirm = FakeConnection(FakeResponse(b'{"level":49}'))
        with patch.object(http_volume.http.client, "HTTPConnection", return_value=write), patch.object(http_volume.http.client, "HTTPSConnection", return_value=confirm):
            with self.assertRaisesRegex(http_volume.HttpVolumeError, "confirm"):
                output.write_volume(50)


if __name__ == "__main__":
    unittest.main()
