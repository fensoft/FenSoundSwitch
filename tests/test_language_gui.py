from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import localization
from gui import MonitorVolumeApp
from localization import set_language, tr, translate_source
from web_presentation import UserActionError


class LanguageGUITests(unittest.TestCase):
    def tearDown(self) -> None:
        set_language("en")

    def test_native_catalog_translates_tray_and_overlay_text(self) -> None:
        set_language("fr")
        self.assertEqual(tr("common.refresh"), "Actualiser")
        self.assertEqual(tr("overlay.route_unavailable"), "La route sélectionnée est indisponible.")
        self.assertEqual(
            translate_source("Living room: Muted; Office: Unmuted"),
            "Living room: Muet; Office: Son activé",
        )
        self.assertEqual(
            translate_source("The selected DDC monitor is unavailable or its identity is ambiguous."),
            "Le moniteur DDC sélectionné est indisponible ou son identité est ambiguë.",
        )
        self.assertEqual(
            translate_source("Active after mouse movement: Playback, Voice output"),
            "Actif après mouvement de la souris: Lecture, Sortie vocale",
        )

    def test_recovery_shell_catalog_is_complete_for_supported_languages(self) -> None:
        keys = set(localization._english)
        set_language("en")
        for key in keys:
            self.assertNotEqual(tr(key), key)
        for language in ("de", "es", "fr", "it"):
            set_language(language)
            with self.subTest(language=language):
                self.assertTrue(keys.issubset(localization._translations[language]))
                for key in keys:
                    self.assertNotEqual(tr(key), key)

    def make_app(self) -> MonitorVolumeApp:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._plugin_manager = Mock()
        app.ui_language_preference = "auto"
        app.ui_language = "en"
        app.set_start_with_windows_enabled = Mock()
        return app

    def test_language_save_does_not_change_autostart_and_recomputes_auto(self) -> None:
        app = self.make_app()
        with patch("gui.save_ui_language") as save, patch(
            "gui.load_ui_language", return_value="auto"
        ), patch("gui.get_user_default_locale_name", return_value="de-DE"):
            self.assertEqual(
                app._dispatch_web_action("settings.save", {"ui_language": "auto"}),
                {"accepted": True},
            )
        save.assert_called_once_with("auto")
        app.set_start_with_windows_enabled.assert_not_called()
        self.assertEqual(app.ui_language_preference, "auto")
        self.assertEqual(app.ui_language, "de")

    def test_explicit_language_ignores_windows_locale(self) -> None:
        app = self.make_app()
        with patch("gui.save_ui_language"), patch(
            "gui.load_ui_language", return_value="fr"
        ), patch("gui.get_user_default_locale_name", return_value="de-DE"):
            app._dispatch_web_action("settings.save", {"ui_language": "fr"})
        self.assertEqual(app.ui_language, "fr")

    def test_language_save_refreshes_existing_recovery_shell_widgets(self) -> None:
        app = self.make_app()
        app._current_legacy_page = "settings"
        app.start_with_windows_var = Mock()
        app.start_with_windows_var.get.return_value = True
        app.brand_subtitle = Mock()
        app.route_nav_button = Mock()
        app.plugin_nav_button = Mock()
        app.integrations_nav_button = Mock()
        app.appearance_nav_button = Mock()
        app.settings_nav_button = Mock()
        app.log_button = Mock()
        app.startup_card = Mock()
        app.start_with_windows_label = Mock()
        app.startup_description_label = Mock()
        app.configuration_actions = Mock()
        app.configuration_description_label = Mock()
        app.export_configuration_button = Mock()
        app.import_configuration_button = Mock()
        app.import_history_button = Mock()
        app.restore_default_button = Mock()
        app.start_with_windows_button = Mock()
        app.routes_panel = Mock()
        app.plugins_panel = Mock()
        app.integrations_panel = Mock()
        app.appearance_panel = Mock()
        app.settings_panel = Mock()
        app.page_title_var = Mock()
        app.page_subtitle_var = Mock()

        with patch("gui.save_ui_language"), patch(
            "gui.load_ui_language", return_value="fr"
        ), patch("gui.get_user_default_locale_name", return_value="de-DE"):
            app._dispatch_web_action("settings.save", {"ui_language": "fr"})

        app.settings_nav_button.configure.assert_any_call(text="Paramètres")
        app.start_with_windows_label.configure.assert_called_with(text="Démarrer avec Windows")
        app.start_with_windows_button.configure.assert_called_with(text="Activé")
        app.page_title_var.set.assert_called_with("Paramètres")
        app.page_subtitle_var.set.assert_called_with(
            "Gérez le démarrage et les sauvegardes de configuration."
        )

    def test_import_confirmation_localizes_chrome_but_preserves_filename(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app.root = Mock()
        app.on_close = Mock()
        app._set_status = Mock()
        app.restart_requested = False
        source = Path("Living Room.fsc")
        set_language("de")

        with patch("gui.messagebox.askyesno", return_value=True) as ask, patch(
            "gui.import_configuration"
        ) as import_archive:
            app._confirm_import_configuration(source)

        ask.assert_called_once_with(
            "FenSoundSwitch neu starten?",
            "Beim Importieren von Living Room.fsc wird FenSoundSwitch geschlossen und neu gestartet. Fortfahren?",
            parent=app.root,
        )
        import_archive.assert_called_once_with(source)
        app._set_status.assert_called_once_with(
            "Konfiguration aus Living Room.fsc importiert. FenSoundSwitch wird neu gestartet..."
        )

    def test_settings_save_rejects_invalid_types_without_mutating_state(self) -> None:
        app = self.make_app()
        for arguments in ({"ui_language": True}, {"start_with_windows": "yes"}):
            with self.subTest(arguments=arguments), self.assertRaises(UserActionError):
                app._dispatch_web_action("settings.save", arguments)
        self.assertEqual((app.ui_language_preference, app.ui_language), ("auto", "en"))

    def test_web_action_boundary_localizes_plugin_errors_and_preserves_details(self) -> None:
        app = self.make_app()
        detail = "receiver.lan:60128 [WinError 10061]"
        set_language("de")
        with patch.object(
            MonitorVolumeApp,
            "_dispatch_web_action",
            side_effect=ValueError(f"Could not communicate with the configured AVR: {detail}"),
        ):
            with self.assertRaises(UserActionError) as raised:
                app._dispatch_localized_web_action("plugin.action", {})
        self.assertIn("Kommunikation", str(raised.exception))
        self.assertTrue(str(raised.exception).endswith(detail))

    def test_web_action_boundary_localizes_plugin_result_messages(self) -> None:
        app = self.make_app()
        set_language("it")
        with patch.object(
            MonitorVolumeApp,
            "_dispatch_web_action",
            return_value={"status": "complete", "message": "Discord authorization was reset."},
        ):
            result = app._dispatch_localized_web_action("plugin.action", {})
        self.assertEqual(result["message"], "L’autorizzazione Discord è stata reimpostata.")

    def test_status_keeps_english_control_source_but_localizes_native_text(self) -> None:
        app = self.make_app()
        app.status_var = Mock()
        set_language("es")
        app._set_status("Discord setup is incomplete")
        self.assertEqual(app._status_source, "Discord setup is incomplete")
        app.status_var.set.assert_called_once()
        self.assertNotEqual(app.status_var.set.call_args.args[0], app._status_source)


if __name__ == "__main__":
    unittest.main()
