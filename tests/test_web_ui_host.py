from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

import web_ui_host


class WebUiBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authkey = bytes(range(32))
        self.encoded_key = base64.urlsafe_b64encode(self.authkey).decode("ascii").rstrip("=")
        self.pipe = r"\\.\pipe\fensoundswitch-web-1234"

    def test_parser_accepts_only_named_pipe_and_bounded_base64url_key(self) -> None:
        parsed = web_ui_host.parse_bootstrap(
            ["--pipe", self.pipe],
            {web_ui_host.AUTHKEY_ENVIRONMENT_VARIABLE: self.encoded_key},
        )

        self.assertEqual(parsed.pipe, self.pipe)
        self.assertEqual(parsed.authkey, self.authkey)
        self.assertEqual(
            web_ui_host.parse_bootstrap(
                ["--pipe", self.pipe],
                {web_ui_host.AUTHKEY_ENVIRONMENT_VARIABLE: self.encoded_key},
            ), parsed)

    def test_parser_rejects_missing_duplicate_unknown_and_positional_arguments(self) -> None:
        invalid = (
            [],
            ["--authkey", self.encoded_key],
            ["--pipe", self.pipe, "extra"],
        )
        for argv in invalid:
            with self.subTest(argv=argv), self.assertRaises(web_ui_host.BootstrapError):
                web_ui_host.parse_bootstrap(
                    argv,
                    {web_ui_host.AUTHKEY_ENVIRONMENT_VARIABLE: self.encoded_key},
                )

    def test_parser_rejects_non_pipe_addresses_and_bad_keys(self) -> None:
        invalid_pipes = (
            "localhost:8080",
            r"C:\temp\socket",
            r"\\server\pipe\shared",
            r"\\.\pipe\..\escape",
        )
        for pipe in invalid_pipes:
            with self.subTest(pipe=pipe), self.assertRaises(web_ui_host.BootstrapError):
                web_ui_host.parse_bootstrap(
                    ["--pipe", pipe],
                    {web_ui_host.AUTHKEY_ENVIRONMENT_VARIABLE: self.encoded_key},
                )
        for key in ("short", "!" * 43, "A" * 87):
            with self.subTest(key=key), self.assertRaises(web_ui_host.BootstrapError):
                web_ui_host.parse_bootstrap(
                    ["--pipe", self.pipe],
                    {web_ui_host.AUTHKEY_ENVIRONMENT_VARIABLE: key},
                )


class WebUiProtocolTests(unittest.TestCase):
    def test_request_is_compact_json_with_strict_method_and_object_params(self) -> None:
        payload = web_ui_host.make_request(7, "snapshot.get", {"revision": 4})

        self.assertEqual(
            json.loads(payload),
            {"id": 7, "method": "snapshot.get", "params": {"revision": 4}},
        )
        self.assertNotIn(b" ", payload)
        for method in ("", "Snapshot.get", "snapshot/get", "a" * 97):
            with self.subTest(method=method), self.assertRaises(web_ui_host.ProtocolError):
                web_ui_host.make_request(1, method, {})
        with self.assertRaises(web_ui_host.ProtocolError):
            web_ui_host.make_request(1, "snapshot.get", [])

    def test_request_rejects_nonfinite_and_oversized_json(self) -> None:
        with self.assertRaises(web_ui_host.ProtocolError):
            web_ui_host.make_request(1, "route.save", {"value": float("nan")})
        with self.assertRaises(web_ui_host.ProtocolError):
            web_ui_host.make_request(
                1,
                "route.save",
                {"value": "x" * web_ui_host.MAX_MESSAGE_BYTES},
            )

    def test_response_requires_matching_id_and_bounded_envelope(self) -> None:
        self.assertEqual(
            web_ui_host.parse_response(b'{"id":2,"ok":true,"result":{"revision":1}}', 2),
            {"revision": 1},
        )
        invalid = (
            b'{"id":3,"ok":true}',
            b'{"id":2,"ok":"yes"}',
            b'{"id":2,"ok":true,"extra":1}',
            b"not-json",
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(web_ui_host.ProtocolError):
                web_ui_host.parse_response(payload, 2)
        with self.assertRaisesRegex(web_ui_host.ProtocolError, "not_found"):
            web_ui_host.parse_response(
                b'{"id":2,"ok":false,"error":{"code":"not_found","message":"Missing"}}',
                2,
            )

    def test_messages_reject_excessive_depth_and_collection_size(self) -> None:
        nested: object = None
        for _ in range(web_ui_host.MAX_MESSAGE_DEPTH + 2):
            nested = [nested]
        with self.assertRaises(web_ui_host.ProtocolError):
            web_ui_host.make_request(1, "dispatch_action", {"nested": nested})
        with self.assertRaises(web_ui_host.ProtocolError):
            web_ui_host.make_request(
                1,
                "dispatch_action",
                {"items": list(range(web_ui_host.MAX_COLLECTION_ITEMS + 1))},
            )

    def test_web_api_adapts_revisioned_snapshots_and_generic_actions(self) -> None:
        bridge = Mock()
        bridge.request.side_effect = (
            {"revision": 4, "changed": True, "snapshot": {"routes": []}},
            {"revision": 4, "changed": False},
            {"accepted": True},
        )
        api = web_ui_host.WebApi(bridge)

        first = api.request("snapshot.get", {"revision": -1})
        unchanged = api.request("snapshot.get", {"revision": 4})
        action = api.request("route.save", {"id": "route-1"})

        self.assertEqual(
            first,
            {"ok": True, "result": {"routes": [], "revision": 4, "changed": True}},
        )
        self.assertEqual(unchanged, {"ok": True, "result": {"revision": 4, "changed": False}})
        self.assertEqual(action, {"ok": True, "result": {"accepted": True}})
        self.assertEqual(
            bridge.request.call_args_list[0].args,
            ("ready", {}),
        )
        self.assertEqual(
            bridge.request.call_args_list[2].args,
            ("dispatch_action", {"action": "route.save", "arguments": {"id": "route-1"}}),
        )

    def test_visible_snapshot_restores_the_window_only_on_transition(self) -> None:
        bridge = Mock()
        bridge.request.side_effect = (
            {"revision": 1, "changed": True, "snapshot": {"presentation": {"visible": True}}},
            {"revision": 2, "changed": True, "snapshot": {"presentation": {"visible": True}}},
        )
        api = web_ui_host.WebApi(bridge)
        window = Mock()
        api.attach_window(window, Mock())

        api.request("snapshot.get", {"revision": -1})
        api.request("snapshot.get", {"revision": 1})

        window.restore.assert_called_once_with()
        window.show.assert_called_once_with()

    def test_save_picker_restores_window_and_returns_selected_path(self) -> None:
        api = web_ui_host.WebApi(Mock())
        window = Mock()
        window.create_file_dialog.return_value = (r"C:\tmp\config.fsc",)
        webview = Mock()
        webview.FileDialog.SAVE = 30
        api.attach_window(window, webview)

        result = api.pick_save_file({"directory": r"C:\Users\tester\AppData\Roaming\fensoundswitch\configurations", "filename": "FenSoundSwitch.fsc", "file_types": ["FenSoundSwitch configuration (*.fsc)"]})

        self.assertEqual(result, {"ok": True, "result": r"C:\tmp\config.fsc"})
        window.restore.assert_called_once_with()
        window.show.assert_called_once_with()
        window.create_file_dialog.assert_called_once_with(30, directory=r"C:\Users\tester\AppData\Roaming\fensoundswitch\configurations", save_filename="FenSoundSwitch.fsc", file_types=("FenSoundSwitch configuration (*.fsc)",))

    def test_open_picker_uses_supplied_directory(self) -> None:
        api = web_ui_host.WebApi(Mock())
        window = Mock()
        window.create_file_dialog.return_value = (r"C:\tmp\config.fsc",)
        webview = Mock()
        webview.FileDialog.OPEN = 20
        api.attach_window(window, webview)

        result = api.pick_open_file({"directory": r"C:\Users\tester\AppData\Roaming\fensoundswitch\configurations", "file_types": ["FenSoundSwitch configuration (*.fsc)"]})

        self.assertEqual(result, {"ok": True, "result": r"C:\tmp\config.fsc"})
        window.create_file_dialog.assert_called_once_with(20, directory=r"C:\Users\tester\AppData\Roaming\fensoundswitch\configurations", save_filename="", file_types=("FenSoundSwitch configuration (*.fsc)",))


class WebUiAssetTests(unittest.TestCase):
    def test_native_window_uses_bundled_application_icon(self) -> None:
        native = Mock()
        window = Mock(native=native)
        icon = object()
        factory = Mock(return_value=icon)

        self.assertIs(web_ui_host.apply_window_icon(window, factory), icon)
        factory.assert_called_once_with(str(web_ui_host.WINDOW_ICON_PATH))
        self.assertIs(native.Icon, icon)

    def test_assets_resolve_only_inside_bundled_web_directory(self) -> None:
        for name in web_ui_host.ASSET_NAMES:
            path = web_ui_host.resolve_asset(name)
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, web_ui_host.ASSET_ROOT)
            self.assertTrue(web_ui_host.is_allowed_navigation(path.as_uri()))
        for name in ("../app.py", "missing.js", ""):
            with self.subTest(name=name), self.assertRaises((ValueError, FileNotFoundError)):
                web_ui_host.resolve_asset(name)
        self.assertFalse(web_ui_host.is_allowed_navigation("https://example.com/"))
        self.assertFalse(web_ui_host.is_allowed_navigation("data:text/html,hello"))
        self.assertFalse(web_ui_host.is_allowed_navigation(Path(__file__).as_uri()))

    def test_html_has_strict_csp_local_assets_and_semantic_application_landmarks(self) -> None:
        html = web_ui_host.resolve_asset("index.html").read_text(encoding="utf-8")

        self.assertIn("default-src 'none'", html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn("form-action 'none'", html)
        self.assertIn('href="app.css"', html)
        self.assertIn('src="app.js"', html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        for contract in ("<nav", "<main", "<dialog", 'aria-live="polite"', "Skip to content"):
            self.assertIn(contract, html)
        self.assertLess(html.index('data-page="settings"'), html.index('data-page="diagnostics"'))
        self.assertLess(html.index('data-page="diagnostics"'), html.index("</nav>"))

    def test_runtime_document_inlines_hashed_assets_without_network_server(self) -> None:
        document = web_ui_host.render_document()

        self.assertIn("script-src 'sha256-", document)
        self.assertIn("style-src 'sha256-", document)
        self.assertIn("<style>", document)
        self.assertIn("<script>", document)
        self.assertLess(document.index('id="workspace"'), document.index("<script>"))
        self.assertNotIn('href="app.css"', document)
        self.assertNotIn('src="app.js"', document)

    def test_assets_cover_pages_bridge_dialogs_and_accessibility_modes(self) -> None:
        html = web_ui_host.resolve_asset("index.html").read_text(encoding="utf-8")
        script = web_ui_host.resolve_asset("app.js").read_text(encoding="utf-8")
        styles = web_ui_host.resolve_asset("app.css").read_text(encoding="utf-8")

        for page in ("routes", "actions", "integrations", "appearance", "settings", "diagnostics", "about"):
            self.assertIn(f"{page}:", script)
        self.assertIn('M2.5 12s3.5-6 9.5-6', html)
        self.assertNotIn('M12 3a9 9 0 1 0 0 18', html)
        for method in ("snapshot.get", "route.save", "route.delete", "signal.save", "signal.delete", "signal.run", "slot.ui", "slot.action", "slot.save", "mqtt.profile.save", "mqtt.profile.delete", "action.save"):
            self.assertIn(method, script)
        self.assertIn("pick_open_file", script)
        self.assertIn("pick_save_file", script)
        self.assertIn("configuration_directory", script)
        self.assertIn('pick_open_file({ title: "Import FenSoundSwitch configuration", directory,', script)
        self.assertIn('event.submitter?.value === "cancel"', script)
        self.assertIn('#editor-close, #editor-cancel', script)
        self.assertNotIn("if (shortcut) buttons.unshift", script)
        self.assertIn('type === "hotkey"', script)
        self.assertIn('type === "sequence"', script)
        self.assertIn('type === "trigger-list"', script)
        self.assertIn("renderTriggerListField", script)
        self.assertIn("groupEditorFields", script)
        self.assertIn('["host", "port"]', script)
        self.assertIn('["username", "password"]', script)
        self.assertIn("homeAssistantId", script)
        self.assertIn('normalize("NFKD")', script)
        self.assertIn('data-autogenerated', script)
        self.assertIn('text: "Add trigger"', script)
        self.assertIn('title: "Remove trigger"', script)
        self.assertIn("_triggerValue", script)
        self.assertIn("syncConditionalFields", script)
        self.assertIn("openMqttIntegration", script)
        self.assertIn("mqtt-profile-list", html)
        self.assertIn("sequence-summary", script)
        self.assertIn("automation-summary", script)
        self.assertIn('#editor-dialog[data-kind="signal"]', styles)
        self.assertIn("grid-auto-rows: max-content", styles)
        self.assertNotIn('height: min(820px, calc(100vh - 38px))', styles)
        self.assertIn("select[data-depends-on]", script)
        self.assertIn("option.when", script)
        self.assertIn("openSlotEditor", script)
        self.assertIn("openChoiceDialog", script)
        self.assertIn('id="choice-dialog"', html)
        self.assertIn('id="editor-description"', html)
        self.assertIn('class: "choice-card"', script)
        self.assertIn('class: "sequence-action"', script)
        self.assertIn('label: "Wait"', script)
        self.assertIn("Pauses the automation", script)
        self.assertNotIn('aria-label": "Action step"', script)
        self.assertNotIn('text: "Add wait step"', script)
        self.assertIn("option.disabled", script)
        self.assertIn('slotDialog.addEventListener("close"', script)
        self.assertIn("Discovering monitor inputs. Please wait...", script)
        self.assertIn('id="slot-refresh"', html)
        self.assertIn("Add at least one action or wait step.", script)
        self.assertIn('data-hotkey', script)
        self.assertIn("return 111 + number", script)
        self.assertIn('145: "Scroll Lock"', script)
        self.assertIn('event.getModifierState?.("AltGraph")', script)
        self.assertIn('text: "↓"', script)
        self.assertIn('class: `split-menu${recent.length', script)
        self.assertIn('" is-empty"', script)
        self.assertIn('text: "▼"', script)
        self.assertIn('target.hidden = mode === "online"', script)
        self.assertIn("wasAtBottom", script)
        self.assertIn('diagnostics: ["Diagnostics", "Review bounded application health information.", ""]', script)
        self.assertIn("previous.textContent !== text", script)
        self.assertIn('id="editor-cancel" type="button"', html)
        self.assertIn('id="slot-dialog"', html)
        self.assertIn('data-page="about"', html)
        self.assertIn("renderAbout", script)
        self.assertIn("application.version", script)
        self.assertIn(".about-card", styles)
        self.assertIn(".trigger-row", styles)
        self.assertIn(".field-row.is-host-port", styles)
        self.assertIn(".choice-card", styles)
        self.assertIn(".dialog-description.tutorial", styles)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("sessionStorage", script)
        self.assertNotIn("console.", script)
        self.assertIn("prefers-color-scheme: dark", styles)
        self.assertIn("forced-colors: active", styles)
        self.assertIn(".connection[hidden]", styles)
        self.assertIn("prefers-reduced-motion: reduce", styles)


if __name__ == "__main__":
    unittest.main()
