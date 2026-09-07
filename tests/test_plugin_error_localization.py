from __future__ import annotations

import ast
import unittest
from pathlib import Path

from localization import set_language, translate_source


ROOT = Path(__file__).resolve().parents[1]


def _template(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.FormattedValue):
        return "DETAIL"
    if isinstance(node, ast.JoinedStr):
        parts = [_template(value) for value in node.values]
        return "".join(part or "" for part in parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _template(node.left), _template(node.right)
        return None if left is None or right is None else left + right
    return None


def bundled_error_templates() -> set[str]:
    messages: set[str] = set()
    for path in (ROOT / "plugins").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in (item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))):
            if function.name.endswith(("summary", "status", "available", "availability")):
                for returned in (item for item in ast.walk(function) if isinstance(item, ast.Return)):
                    candidates = returned.value.elts if isinstance(returned.value, (ast.Tuple, ast.List)) else (returned.value,)
                    for candidate in candidates:
                        if candidate is not None:
                            value = _template(candidate)
                            if value and any(
                                marker in value.casefold()
                                for marker in ("unavailable", "invalid", "not configured", "error", "failed")
                            ):
                                messages.add(value)
        for node in ast.walk(tree):
            candidate: ast.AST | None = None
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                candidate = node.exc.args[0]
            elif isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else ""
                attribute = node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if attribute == "report_status" and node.args:
                    candidate = node.args[0]
                elif name == "plugin_ui_result":
                    message = next((item.value for item in node.keywords if item.arg == "message"), None)
                    candidate = message
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                if any(isinstance(target, ast.Attribute) and target.attr.endswith(("error", "reason")) for target in targets):
                    candidate = node.value
            if candidate is not None:
                value = _template(candidate)
                if value and any(character.isalpha() for character in value):
                    messages.add(value)
    return messages


class BundledPluginErrorLocalizationTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_language("en")

    def test_ast_inventory_has_a_translation_for_every_deterministic_template(self) -> None:
        messages = bundled_error_templates()
        # Quarantined candidates under new/plugins are intentionally outside
        # the active bundled-plugin translation inventory.
        self.assertGreaterEqual(len(messages), 200)
        for language in ("de", "es", "fr", "it"):
            set_language(language)
            missing = sorted(message for message in messages if translate_source(message) == message)
            with self.subTest(language=language):
                self.assertEqual(missing, [])

    def test_english_is_the_source_fallback(self) -> None:
        set_language("en")
        message = "Discord is not running or no local RPC pipe is available"
        self.assertEqual(translate_source(message), message)

    def test_discord_rpc_unavailable_preserves_the_pipe_detail(self) -> None:
        detail = r"\\.\pipe\discord-ipc-7 (WinError 231)"
        set_language("fr")
        translated = translate_source(
            f"Discord is not running or no local RPC pipe is available: {detail}"
        )
        self.assertTrue(translated.startswith("Discord n’est pas en cours d’exécution"))
        self.assertTrue(translated.endswith(detail))

    def test_every_receiver_wrapper_preserves_opaque_socket_details(self) -> None:
        wrappers = (
            "AVR",
            "Onkyo receiver",
            "Pioneer/Elite receiver",
            "Sony receiver",
            "Yamaha receiver",
        )
        detail = "living-room.local:60128 [WinError 10061]"
        for language in ("de", "es", "fr", "it"):
            set_language(language)
            for receiver in wrappers:
                source = f"Could not communicate with the configured {receiver}: {detail}"
                translated = translate_source(source)
                with self.subTest(language=language, receiver=receiver):
                    self.assertNotEqual(translated, source)
                    self.assertTrue(translated.endswith(detail))


if __name__ == "__main__":
    unittest.main()
