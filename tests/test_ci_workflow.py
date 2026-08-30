from __future__ import annotations

import unittest
from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
)


class CIWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_workflow_covers_supported_python_boundary_on_windows(self) -> None:
        self.assertIn("runs-on: windows-latest", self.workflow)
        self.assertIn('python-version: ["3.10", "3.14"]', self.workflow)
        self.assertIn("uses: actions/checkout@v6", self.workflow)
        self.assertIn("uses: actions/setup-python@v6", self.workflow)
        self.assertIn("permissions:\n  contents: read", self.workflow)

    def test_workflow_runs_every_low_risk_repository_check(self) -> None:
        expected_commands = (
            "python -m pip install -e .",
            "python -m unittest discover -s tests -v",
            "python -m compileall -q",
            "python -m pip check",
            "Language.Parser]::ParseFile",
            "git diff --check",
            "git diff --cached --check",
            "git status --short",
        )
        for command in expected_commands:
            with self.subTest(command=command):
                self.assertIn(command, self.workflow)
        self.assertIn("autostart.py", self.workflow)
        self.assertIn("audio_outputs.py", self.workflow)
        self.assertIn("diagnostics.py", self.workflow)
        self.assertIn("ddc_volume_plugin.py", self.workflow)
        self.assertIn("discord_output_plugin.py", self.workflow)
        self.assertIn("plugin_api.py", self.workflow)
        self.assertIn("plugin_hotkeys.py", self.workflow)
        self.assertIn("plugin_manager.py", self.workflow)

    def test_workflow_never_launches_the_app_or_hardware_tools(self) -> None:
        forbidden_commands = (
            "python app.py",
            "monitorcontrol",
            "run: .\\build_exe.ps1",
            "enumerate_monitors(",
            "set_monitor_volume(",
            "enumerate_audio_render_endpoints(",
            "reconcile_monitor_audio_outputs(",
        )
        for command in forbidden_commands:
            with self.subTest(command=command):
                self.assertNotIn(command, self.workflow)

    def test_release_workflow_builds_master_but_publishes_only_tags(self) -> None:
        workflow = RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("branches:\n      - master", workflow)
        self.assertIn('tags:\n      - "**"', workflow)
        self.assertIn("python -m pip install -e .[build]", workflow)
        self.assertIn("run: .\\build_exe.ps1", workflow)
        self.assertIn("dist\\windows-ddc.exe", workflow)
        self.assertIn("if: github.ref == 'refs/heads/master'", workflow)
        self.assertIn("if: startsWith(github.ref, 'refs/tags/')", workflow)
        self.assertIn("permissions:\n      contents: read", workflow)
        self.assertIn("permissions:\n      contents: write", workflow)
        self.assertIn("softprops/action-gh-release@v2", workflow)


if __name__ == "__main__":
    unittest.main()
