from __future__ import annotations

import ast
import base64
import json
import re
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

    def test_picker_passes_localized_title_when_dialog_api_supports_it(self) -> None:
        calls = []

        class TitleWindow:
            def restore(self):
                pass

            def show(self):
                pass

            def create_file_dialog(self, kind, *, directory, save_filename, file_types, title):
                calls.append((kind, directory, save_filename, file_types, title))
                return (r"C:\tmp\config.fsc",)

        api = web_ui_host.WebApi(Mock())
        webview = Mock()
        webview.FileDialog.OPEN = 20
        api.attach_window(TitleWindow(), webview)

        result = api.pick_open_file({
            "title": "Konfiguration importieren",
            "directory": r"C:\tmp",
            "file_types": ["FenSoundSwitch-Konfiguration (*.fsc)"],
        })

        self.assertEqual(result, {"ok": True, "result": r"C:\tmp\config.fsc"})
        self.assertEqual(calls, [(20, r"C:\tmp", "", ("FenSoundSwitch-Konfiguration (*.fsc)",), "Konfiguration importieren")])


class WebUiAssetTests(unittest.TestCase):
    def test_translation_catalogs_cover_every_english_key(self) -> None:
        source = web_ui_host.resolve_asset("i18n.js").read_text(encoding="utf-8")
        locales = ("en", "de", "es", "fr", "it")
        starts = {locale: source.index(f"  {locale}: {{") for locale in locales}
        sections = {}
        for index, locale in enumerate(locales):
            end = starts[locales[index + 1]] if index + 1 < len(locales) else source.index("\n};\n\nconst SOURCE_TRANSLATIONS")
            sections[locale] = source[starts[locale]:end]
        keys = {locale: set(re.findall(r'"([^"]+)":', section)) for locale, section in sections.items()}
        self.assertTrue(keys["en"])
        for locale in locales[1:]:
            self.assertEqual(keys[locale], keys["en"])

        for table_name, next_name in (
            ("SOURCE_TRANSLATIONS", "MODEL_TRANSLATIONS"),
            ("MODEL_TRANSLATIONS", "PLUGIN_TRANSLATIONS"),
            ("PLUGIN_TRANSLATIONS", "PLUGIN_DESCRIPTION_TRANSLATIONS"),
            ("PLUGIN_DESCRIPTION_TRANSLATIONS", "RECEIVER_TRANSLATIONS"),
            ("RECEIVER_TRANSLATIONS", "RESCAN_TRANSLATIONS"),
            ("RESCAN_TRANSLATIONS", "BUNDLED_UI_TRANSLATIONS"),
            ("BUNDLED_UI_TRANSLATIONS", "HOST_ERROR_TRANSLATIONS"),
            ("HOST_ERROR_TRANSLATIONS", "STATUS_TRANSLATIONS"),
            ("STATUS_TRANSLATIONS", "STATUS_PREFIX_TRANSLATIONS"),
            ("STATUS_PREFIX_TRANSLATIONS", "RUNTIME_TRANSLATIONS"),
            ("RUNTIME_TRANSLATIONS", "DECORATION_TRANSLATIONS"),
            ("DECORATION_TRANSLATIONS", "GUI_STATUS_TRANSLATIONS"),
        ):
            table = source[source.index(f"const {table_name}"):source.index(f"const {next_name}")]
            table_starts = {locale: table.index(f"  {locale}: {{") for locale in locales[1:]}
            table_keys = {}
            translated_locales = locales[1:]
            for index, locale in enumerate(translated_locales):
                end = table_starts[translated_locales[index + 1]] if index + 1 < len(translated_locales) else table.rindex("\n};")
                table_keys[locale] = set(re.findall(r'"([^"]+)":', table[table_starts[locale]:end]))
            for locale in translated_locales[1:]:
                self.assertEqual(table_keys[locale], table_keys[translated_locales[0]], f"{table_name} {locale}")

        for bundled_text in (
            "Windows media keys",
            "Custom keyboard keys",
            "DDC monitor volume",
            "Windows Bluetooth volume",
            "Windows soundcard volume",
            "Windows capture gain",
            "Audio output keep-alive",
            "Discord output switch",
            "Windows 11 overlay",
            "Decrease",
            "Increase",
            "Mute (optional)",
            "MQTT profile",
            "Host or IP address",
            "Bluetooth audio device",
            "Default playback output",
            "Client secret",
            "Broker host",
            "App start",
            "Key press",
            "Tray menu option",
            "Reset authorization",
            "Open Developer Portal",
            "Save and authorize",
            "Selects and verifies one configured monitor input through DDC/CI.",
            "Cycle Windows playback",
            "Configure DDC monitor input",
        ):
            self.assertIn(f'"{bundled_text}":', source)

    def test_bundled_static_plugin_ui_text_has_translation_entries(self) -> None:
        translations = web_ui_host.resolve_asset("i18n.js").read_text(encoding="utf-8")
        translated_keys = set(re.findall(r'"((?:\\.|[^"\\])+)":', translations))
        root = Path(__file__).resolve().parents[1]
        static_text: set[str] = set()

        def constants(node: ast.AST) -> set[str]:
            return {
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and child.value.strip()
            }

        for path in (root / "plugins").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for statement in node.body:
                        if isinstance(statement, ast.Assign) and any(
                            isinstance(target, ast.Name) and target.id in {"name", "description", "input_name", "provider_name"}
                            for target in statement.targets
                        ):
                            static_text.update(constants(statement.value))
                if not isinstance(node, ast.Call):
                    continue
                function_name = node.func.id if isinstance(node.func, ast.Name) else ""
                if function_name == "SlotAction":
                    for argument in node.args[1:3]:
                        static_text.update(constants(argument))
                elif function_name == "plugin_ui_document":
                    if node.args:
                        static_text.update(constants(node.args[0]))
                    if len(node.args) > 3:
                        static_text.update(constants(node.args[3]))
                    for collection in node.args[1:3]:
                        for item in ast.walk(collection):
                            if not isinstance(item, ast.Dict):
                                continue
                            for key, value in zip(item.keys, item.values):
                                if isinstance(key, ast.Constant) and key.value in {"label", "description", "confirm"}:
                                    static_text.update(constants(value))

        # Brand/protocol-only labels are intentionally stable and user/device
        # values are non-literals, so every remaining static phrase must be in
        # at least one exact source translation table.
        intentionally_stable = {
            "MQTT / Home Assistant", "MQTT / HA", "FenSoundSwitch",
            "Alt", "Shift", "Win", "Tab", "Pause", "Escape", "End", "Home", "Menu",
            "error", ".", "OAuth status: ",
            "1. Open the Discord Developer Portal and create or select an application.\n2. Under OAuth2, add ",
            " as an exact redirect URI.\n3. Reset the client secret, then paste the new secret below.\n4. Copy the application's Application ID below.\n5. Select Save and authorize, then approve only rpc, rpc.voice.read, and rpc.voice.write in Discord.",
        }
        missing = sorted(text for text in static_text - intentionally_stable if text not in translated_keys)
        self.maxDiff = None
        self.assertEqual(missing, [])

    def test_renderer_translates_static_enums_but_preserves_discovered_names(self) -> None:
        script = web_ui_host.resolve_asset("app.js").read_text(encoding="utf-8")

        self.assertIn("function optionText(field, option)", script)
        self.assertIn('baseKey === "route_type" || baseKey === "mode" || baseKey === "startup_input"', script)
        self.assertIn('const baseKey = key.split("__").at(-1)', script)
        self.assertIn('endpointId.startsWith("fensoundswitch:")', script)
        self.assertIn("return isTranslatedEnum ? sourceText(label) : label", script)
        self.assertNotIn('input.append(element("option", { value: encoded, text: sourceText', script)

    def test_dynamic_translation_preserves_opaque_status_tails(self) -> None:
        translations = web_ui_host.resolve_asset("i18n.js").read_text(encoding="utf-8")

        self.assertIn("const DYNAMIC_TRANSLATIONS", translations)
        self.assertIn("const tutorial = /^1\\. Open the Discord Developer Portal", translations)
        self.assertIn("// Unknown tails remain byte-for-byte device, user, or exception text.", translations)
        self.assertIn("let tail = i18nSource(colon[2])", translations)
        self.assertIn("return `${prefix}: ${tail}`", translations)
        self.assertIn("const summarizedAction", translations)
        self.assertIn("tail = `${action} (${i18nSource(summarizedAction[2])})`", translations)
        self.assertIn('value.split(" + ").map(i18nSource)', translations)
        self.assertIn('value.split("+")', translations)
        self.assertIn('^Found (\\d+) configurable DDC monitor', translations)
        self.assertIn('^Route (.+) is unavailable: (.+)$', translations)
        self.assertIn('^Automation (.+) trigger failed: (.+)$', translations)
        self.assertIn('^Plugin (.+) is unavailable: (.+)$', translations)
        self.assertIn('Pioneer\\/Elite receiver|Sony receiver', translations)
        self.assertIn('return `${wrapper}: ${match[2]}`', translations)
        self.assertIn('t("error.requestDetail", { detail: raw })', web_ui_host.resolve_asset("app.js").read_text(encoding="utf-8"))
        self.assertIn('name: sourceText(safeText(entity.name, localizedKind))', web_ui_host.resolve_asset("app.js").read_text(encoding="utf-8"))

    def test_runtime_status_catalog_covers_discord_and_ddc_surfaces(self) -> None:
        translations = web_ui_host.resolve_asset("i18n.js").read_text(encoding="utf-8")

        for text in (
            "Switching output…",
            "Checking Discord authorization…",
            "Discord Developer Portal opened.",
            "Discord authorization started.",
            "Discord authorization was reset.",
            "Setup required",
            "Discord is not running or no local RPC pipe is available",
            "Another DDC operation is running. Select Refresh monitors to retry.",
            "Monitor discovery could not be started.",
        ):
            self.assertGreaterEqual(translations.count(f'"{text}":'), 4)

        discord_source = Path(__file__).resolve().parents[1].joinpath(
            "plugins", "discord_output_plugin.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Discord is not running or no local RPC pipe is available", discord_source)
        self.assertIn(
            "// Pipe names and OS error details are opaque and must not be translated.",
            translations,
        )
        self.assertIn('return `${runtime[match[1]]}: ${match[2]}`', translations)

    def test_automation_cards_translate_structured_parts_without_parsing_user_text(self) -> None:
        script = web_ui_host.resolve_asset("app.js").read_text(encoding="utf-8")
        gui_source = Path(__file__).resolve().parents[1].joinpath("gui.py").read_text(encoding="utf-8")
        manager_source = Path(__file__).resolve().parents[1].joinpath("plugin_manager.py").read_text(encoding="utf-8")

        self.assertIn('"plugin_name": record.name', manager_source)
        self.assertIn('"action_label": action.label', manager_source)
        self.assertIn('"summary_translatable": summary in', gui_source)
        self.assertIn('const pluginName = sourceText(safeText(slot.plugin_name))', script)
        self.assertIn('slot.summary_translatable ? sourceText', script)
        self.assertIn('trigger.kind === "tray"', script)
        self.assertIn('safeText(trigger.label)', script)

    def test_host_generated_web_validation_errors_have_translation_entries(self) -> None:
        translations = web_ui_host.resolve_asset("i18n.js").read_text(encoding="utf-8")
        translated_keys = set(re.findall(r'"((?:\\.|[^"\\])+)":', translations))
        english_catalog = translations[translations.index("  en: {"):translations.index("  de: {")]
        translated_values = set(re.findall(r': "((?:\\.|[^"\\])+)"', english_catalog))
        gui_source = Path(__file__).resolve().parents[1].joinpath("gui.py").read_text(encoding="utf-8")
        tree = ast.parse(gui_source)
        dispatch = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_dispatch_web_action"
        )
        messages = {
            child.value
            for child in ast.walk(dispatch)
            if isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and (child.value.endswith(".") or child.value.endswith("?"))
            and " " in child.value
        }
        self.maxDiff = None
        self.assertEqual(sorted(messages - translated_keys - translated_values), [])

    def test_static_gui_snapshot_statuses_have_translation_entries(self) -> None:
        translations = web_ui_host.resolve_asset("i18n.js").read_text(encoding="utf-8")
        translated_keys = set(re.findall(r'"((?:\\.|[^"\\])+)":', translations))
        gui_source = Path(__file__).resolve().parents[1].joinpath("gui.py").read_text(encoding="utf-8")
        tree = ast.parse(gui_source)
        messages: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_set_status" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                messages.add(node.args[0].value)
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Attribute) and target.attr == "_control_unavailable_reason" for target in node.targets) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                messages.add(node.value.value)
        self.maxDiff = None
        self.assertEqual(sorted(messages - translated_keys), [])

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
        self.assertIn('src="i18n.js"', html)
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
        self.assertNotIn('src="i18n.js"', document)
        self.assertNotIn('src="app.js"', document)

    def test_assets_cover_pages_bridge_dialogs_and_accessibility_modes(self) -> None:
        html = web_ui_host.resolve_asset("index.html").read_text(encoding="utf-8")
        script = web_ui_host.resolve_asset("app.js").read_text(encoding="utf-8")
        translations = web_ui_host.resolve_asset("i18n.js").read_text(encoding="utf-8")
        styles = web_ui_host.resolve_asset("app.css").read_text(encoding="utf-8")

        for page in ("routes", "actions", "integrations", "appearance", "settings", "diagnostics", "about"):
            self.assertIn(f"{page}:", script)
        self.assertIn('"wizard.name", "wizard.type", "wizard.inputPlugin"', script)
        self.assertIn('t("wizard.step"', script)
        self.assertIn("wizard.values.route_type", script)
        self.assertIn('M2.5 12s3.5-6 9.5-6', html)
        self.assertNotIn('M12 3a9 9 0 1 0 0 18', html)
        for method in ("snapshot.get", "route.save", "route.endpoint-form", "route.endpoint-action", "route.delete", "signal.save", "signal.delete", "signal.run", "slot.ui", "slot.action", "slot.save", "mqtt.profile.save", "mqtt.profile.delete", "action.save"):
            self.assertIn(method, script)
        self.assertIn("formDocument.actions", script)
        self.assertIn("wizard.documents[endpoint] = result.document", script)
        self.assertIn('action.auto === true', script)
        self.assertIn("pick_open_file", script)
        self.assertIn("pick_save_file", script)
        self.assertIn("configuration_directory", script)
        self.assertIn('pick_open_file({ title: `${t("settings.import")} FenSoundSwitch`, directory,', script)
        self.assertIn('class: "split-control", role: "group"', script)
        self.assertIn('text: t("settings.import"), onclick: importConfiguration', script)
        self.assertIn('class: "button-row configuration-actions"', script)
        self.assertIn('text: t("settings.restore"), title: t("settings.restoreTitle")', script)
        self.assertIn(".configuration-actions { flex: 0 0 auto; flex-wrap: nowrap;", styles)
        self.assertIn('class: "secondary split-arrow", type: "button"', script)
        self.assertIn('arrowButton.setAttribute("aria-expanded"', script)
        self.assertIn("importMenuOpen", script)
        self.assertIn("state.importMenuOpen = !state.importMenuOpen", script)
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
        self.assertIn('text: t("editor.addTrigger")', script)
        self.assertIn('kind === "tray" ? { kind, label: automationName }', script)
        self.assertIn('title: t("editor.removeTrigger")', script)
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
        self.assertIn('label: t("editor.wait")', script)
        self.assertIn('t("editor.actionDescription")', script)
        self.assertNotIn('aria-label": "Action step"', script)
        self.assertNotIn('text: "Add wait step"', script)
        self.assertIn("option.disabled", script)
        self.assertIn('slotDialog.addEventListener("close"', script)
        self.assertIn('t("editor.discovering")', script)
        self.assertIn('id="slot-refresh"', html)
        self.assertIn('t("error.addStep")', script)
        self.assertIn('data-hotkey', script)
        self.assertIn("return 111 + number", script)
        self.assertIn('145: "Scroll Lock"', script)
        self.assertIn('event.getModifierState?.("AltGraph")', script)
        self.assertIn('text: "↓"', script)
        self.assertIn('class: "split-menu"', script)
        self.assertIn('text: "▼"', script)
        self.assertIn('target.hidden = mode === "online"', script)
        self.assertIn("wasAtBottom", script)
        self.assertIn('diagnostics: ["nav.diagnostics", "page.diagnostics.description", ""]', script)
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
        for locale in ("en", "de", "es", "fr", "it"):
            self.assertIn(f"  {locale}: {{", translations)
        for contract in ("I18N_CATALOGS", "SOURCE_TRANSLATIONS", "i18nSetLanguage", "i18nApplyStatic"):
            self.assertIn(contract, translations)
        self.assertIn("resolved_ui_language", script)
        self.assertIn("ui_language: languageSelect.value", script)


if __name__ == "__main__":
    unittest.main()
