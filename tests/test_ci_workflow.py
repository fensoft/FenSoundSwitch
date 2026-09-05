from __future__ import annotations

import unittest
from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
)
BUILD_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "build_exe.ps1"
INSTALLER_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "build_installer.ps1"
INSTALLER_SOURCE_PATH = Path(__file__).resolve().parents[1] / "installer" / "FenSoundSwitch.wxs"
DOTNET_TOOLS_PATH = Path(__file__).resolve().parents[1] / ".config" / "dotnet-tools.json"
PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


class CIWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_workflow_covers_supported_python_boundary_on_windows(self) -> None:
        self.assertIn("runs-on: windows-latest", self.workflow)
        self.assertIn('python-version: ["3.10"]', self.workflow)
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
        self.assertIn("app_version.py", self.workflow)
        self.assertIn("audio_outputs.py", self.workflow)
        self.assertIn("diagnostics.py", self.workflow)
        self.assertIn("plugins", self.workflow)
        self.assertIn("plugin_api.py", self.workflow)
        self.assertIn("plugin_hotkeys.py", self.workflow)
        self.assertIn("plugin_manager.py", self.workflow)
        self.assertIn("web_presentation.py", self.workflow)
        self.assertIn("web_ui_host.py", self.workflow)

    def test_workflow_never_launches_the_app_or_hardware_tools(self) -> None:
        forbidden_commands = (
            "python app.py",
            "monitorcontrol",
            "run: .\\build_exe.ps1",
            "run: .\\build_installer.ps1",
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
        self.assertIn('tags:\n      - "[0-9]*.[0-9]*"', workflow)
        self.assertIn('      - "v[0-9]*.[0-9]*"', workflow)
        self.assertIn("python -m pip install -e .[build]", workflow)
        self.assertIn('python-version: "3.10"', workflow)
        self.assertIn("uses: actions/setup-dotnet@v5", workflow)
        self.assertIn('dotnet-version: "8.0.x"', workflow)
        self.assertIn("run: .\\build_installer.ps1", workflow)
        self.assertIn("dist\\FenSoundSwitch.msi", workflow)
        self.assertIn("files: dist/FenSoundSwitch.msi", workflow)
        self.assertIn("if: github.ref == 'refs/heads/master'", workflow)
        self.assertIn("if: startsWith(github.ref, 'refs/tags/')", workflow)
        self.assertIn("permissions:\n      contents: read", workflow)
        self.assertIn("permissions:\n      contents: write", workflow)
        self.assertIn("softprops/action-gh-release@v2", workflow)
        self.assertIn("FENSOUNDSWITCH_BUILD_VERSION: ${{ github.ref_name }}", workflow)
        self.assertIn(".\\build_installer.ps1 -Version $env:FENSOUNDSWITCH_BUILD_VERSION", workflow)

    def test_build_defaults_to_dev_and_embeds_runtime_and_windows_versions(self) -> None:
        script = BUILD_SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn('[string]$Version = "dev"', script)
        self.assertIn("--file-version=$windowsVersion", script)
        self.assertIn("--product-version=$windowsVersion", script)
        self.assertIn("--standalone", script)
        self.assertNotIn("--onefile", script)
        self.assertIn("[System.IO.Path]::GetTempPath()", script)
        self.assertIn("robocopy", script)
        self.assertLess(script.index("Nuitka standalone output was not found"), script.index("robocopy"))
        self.assertIn("requires a 64-bit Python interpreter", script)
        self.assertIn("--include-data-files=fensoundswitch-version.txt=fensoundswitch-version.txt", script)
        self.assertIn(
            "--include-data-dir=plugins/macos_overlay/assets=plugins/macos_overlay/assets",
            script,
        )
        pyproject = PYPROJECT_PATH.read_text(encoding="utf-8")
        self.assertIn('plugins = ["macos_overlay/assets/*.svg"]', pyproject)
        self.assertIn("[System.IO.File]::WriteAllText", script)
        self.assertIn("Remove-Item -LiteralPath $versionFile", script)
        self.assertNotIn("refusing to overwrite it", script)
        self.assertIn('"Nuitka==4.2"', PYPROJECT_PATH.read_text(encoding="utf-8"))

    def test_installer_is_native_msi_with_upgrade_and_standalone_payload(self) -> None:
        script = INSTALLER_SCRIPT_PATH.read_text(encoding="utf-8")
        source = INSTALLER_SOURCE_PATH.read_text(encoding="utf-8")
        self.assertIn("build_exe.ps1", script)
        self.assertIn("[switch]$SkipStandaloneBuild", script)
        self.assertIn("does not match requested version", script)
        self.assertIn("dist\\FenSoundSwitch.msi", script)
        self.assertIn("ProductVersion=$msiVersion", script)
        self.assertIn("tool restore", script)
        self.assertIn("https://api.nuget.org/v3/index.json", script)
        self.assertIn("tool run wix -- build", script)
        self.assertIn("tool run wix -- msi validate", script)
        self.assertIn('Scope="perMachine"', source)
        self.assertIn('StandardDirectory Id="ProgramMenuFolder"', source)
        self.assertIn('AllowSameVersionUpgrades="yes"', source)
        self.assertIn("<MajorUpgrade", source)
        self.assertIn('Include="!(bindpath.StandaloneDir)\\**"', source)
        self.assertIn('Advertise="yes"', source)
        self.assertIn('<Icon Id="FenSoundSwitch.ico"', source)
        self.assertIn('<Property Id="ARPPRODUCTICON" Value="FenSoundSwitch.ico"', source)
        self.assertIn('Icon="FenSoundSwitch.ico"', source)
        self.assertIn('<Exclude Files="!(bindpath.StandaloneDir)\\FenSoundSwitch.exe"', source)
        self.assertNotIn("Bundle", source)
        self.assertIn('"version": "6.0.2"', DOTNET_TOOLS_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
