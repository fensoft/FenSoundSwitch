"""Process-local translations for native UI surfaces."""
from __future__ import annotations

import re


_language = "en"
_english = {
    "tray.routing": "Routing", "common.enabled": "Enabled", "common.disabled": "Disabled",
    "common.refresh": "Refresh", "common.automations": "Automations", "common.restore": "Restore",
    "common.exit": "Exit", "overlay.volume": "Volume", "common.unavailable": "Unavailable",
    "overlay.monitor_unavailable": "Selected monitor is unavailable.", "common.done": "Done",
    "overlay.no_providers": "No routed volume providers are available.",
    "overlay.route_unavailable": "Selected route is unavailable.",
}
_translations = {
    "de": {"tray.routing": "Routing", "common.enabled": "Aktiviert", "common.disabled": "Deaktiviert", "common.refresh": "Aktualisieren", "common.automations": "Automatisierungen", "common.restore": "Wiederherstellen", "common.exit": "Beenden", "overlay.volume": "Lautstärke", "common.unavailable": "Nicht verfügbar", "overlay.monitor_unavailable": "Der ausgewählte Monitor ist nicht verfügbar.", "common.done": "Fertig", "overlay.no_providers": "Keine gerouteten Lautstärkeanbieter verfügbar.", "overlay.route_unavailable": "Die ausgewählte Route ist nicht verfügbar."},
    "es": {"tray.routing": "Enrutamiento", "common.enabled": "Activado", "common.disabled": "Desactivado", "common.refresh": "Actualizar", "common.automations": "Automatizaciones", "common.restore": "Restaurar", "common.exit": "Salir", "overlay.volume": "Volumen", "common.unavailable": "No disponible", "overlay.monitor_unavailable": "El monitor seleccionado no está disponible.", "common.done": "Hecho", "overlay.no_providers": "No hay proveedores de volumen enrutados disponibles.", "overlay.route_unavailable": "La ruta seleccionada no está disponible."},
    "fr": {"tray.routing": "Routage", "common.enabled": "Activé", "common.disabled": "Désactivé", "common.refresh": "Actualiser", "common.automations": "Automatisations", "common.restore": "Restaurer", "common.exit": "Quitter", "overlay.volume": "Volume", "common.unavailable": "Indisponible", "overlay.monitor_unavailable": "Le moniteur sélectionné est indisponible.", "common.done": "Terminé", "overlay.no_providers": "Aucun fournisseur de volume routé n’est disponible.", "overlay.route_unavailable": "La route sélectionnée est indisponible."},
    "it": {"tray.routing": "Routing", "common.enabled": "Abilitato", "common.disabled": "Disabilitato", "common.refresh": "Aggiorna", "common.automations": "Automazioni", "common.restore": "Ripristina", "common.exit": "Esci", "overlay.volume": "Volume", "common.unavailable": "Non disponibile", "overlay.monitor_unavailable": "Il monitor selezionato non è disponibile.", "common.done": "Fatto", "overlay.no_providers": "Nessun provider di volume instradato disponibile.", "overlay.route_unavailable": "Il percorso selezionato non è disponibile."},
}

_english.update({
    "common.all_files": "All files",
    "common.close": "Close",
    "common.off": "Off",
    "common.on": "On",
    "shell.subtitle": "Audio routing control",
    "shell.searching": "Searching for monitors...",
    "nav.routes": "Routes",
    "nav.integrations": "Integrations",
    "nav.appearance": "Appearance",
    "nav.settings": "Settings",
    "nav.diagnostics": "Diagnostics",
    "page.routes.title": "Audio routes",
    "page.routes.description": "Send each input to the output you want to control.",
    "page.automations.description": "Build ordered steps and choose how they run.",
    "page.integrations.description": "Configure shared connections used by routes and automations.",
    "page.appearance.description": "Choose how volume changes are presented on screen.",
    "page.settings.description": "Manage startup behavior and configuration backups.",
    "settings.startup": "Startup",
    "settings.start_with_windows": "Start with Windows",
    "settings.startup_description": "Launch quietly in the notification area when you sign in.",
    "settings.configuration": "Configuration",
    "settings.configuration_description": "Export a backup, import one, or restore the bundled defaults.",
    "settings.export": "Export",
    "settings.import": "Import",
    "settings.recent": "Recent",
    "settings.restore_default": "Restore default",
    "config.file_type": "FenSoundSwitch configuration",
    "config.export_title": "Export FenSoundSwitch configuration",
    "config.import_title": "Import FenSoundSwitch configuration",
    "config.restart_title": "Restart FenSoundSwitch?",
    "config.restart_message": "Importing {name} will close and restart FenSoundSwitch. Continue?",
    "config.none_available": "No exported configuration is available.",
    "config.default_missing": "Default configuration not found: {path}.",
    "config.exported": "Configuration exported to {path}.",
    "config.exported_history": "Configuration exported to {path} and added to import history.",
    "config.imported_restart": "Configuration imported from {name}. Restarting FenSoundSwitch...",
    "diagnostics.window_title": "FenSoundSwitch diagnostic log",
    "diagnostics.title": "Diagnostic log",
    "diagnostics.description": "Live application events. Sensitive credentials are never written here.",
    "startup.enabled": "Start with Windows enabled.",
    "startup.disabled": "Start with Windows disabled.",
    "startup.enable_failed": "Could not enable Start with Windows: {error}",
    "startup.disable_failed": "Could not disable Start with Windows: {error}",
})

_translations["de"].update({
    "common.all_files": "Alle Dateien", "common.close": "Schließen", "common.off": "Aus", "common.on": "Ein",
    "shell.subtitle": "Steuerung der Audiorouten", "shell.searching": "Monitore werden gesucht...",
    "nav.routes": "Routen", "nav.integrations": "Integrationen", "nav.appearance": "Darstellung", "nav.settings": "Einstellungen", "nav.diagnostics": "Diagnose",
    "page.routes.title": "Audiorouten", "page.routes.description": "Leiten Sie jeden Eingang an den gewünschten steuerbaren Ausgang weiter.",
    "page.automations.description": "Erstellen Sie geordnete Schritte und legen Sie fest, wie sie ausgeführt werden.",
    "page.integrations.description": "Konfigurieren Sie gemeinsame Verbindungen für Routen und Automatisierungen.",
    "page.appearance.description": "Legen Sie fest, wie Lautstärkeänderungen auf dem Bildschirm angezeigt werden.",
    "page.settings.description": "Verwalten Sie das Startverhalten und Konfigurationssicherungen.",
    "settings.startup": "Start", "settings.start_with_windows": "Mit Windows starten", "settings.startup_description": "Beim Anmelden unauffällig im Infobereich starten.",
    "settings.configuration": "Konfiguration", "settings.configuration_description": "Exportieren oder importieren Sie eine Sicherung oder stellen Sie die mitgelieferten Standardwerte wieder her.",
    "settings.export": "Exportieren", "settings.import": "Importieren", "settings.recent": "Zuletzt verwendet", "settings.restore_default": "Standard wiederherstellen",
    "config.file_type": "FenSoundSwitch-Konfiguration", "config.export_title": "FenSoundSwitch-Konfiguration exportieren", "config.import_title": "FenSoundSwitch-Konfiguration importieren",
    "config.restart_title": "FenSoundSwitch neu starten?", "config.restart_message": "Beim Importieren von {name} wird FenSoundSwitch geschlossen und neu gestartet. Fortfahren?",
    "config.none_available": "Keine exportierte Konfiguration verfügbar.", "config.default_missing": "Standardkonfiguration nicht gefunden: {path}.",
    "config.exported": "Konfiguration nach {path} exportiert.", "config.exported_history": "Konfiguration nach {path} exportiert und zum Importverlauf hinzugefügt.",
    "config.imported_restart": "Konfiguration aus {name} importiert. FenSoundSwitch wird neu gestartet...",
    "diagnostics.window_title": "FenSoundSwitch-Diagnoseprotokoll", "diagnostics.title": "Diagnoseprotokoll", "diagnostics.description": "Aktuelle Anwendungsereignisse. Vertrauliche Anmeldedaten werden hier nie protokolliert.",
    "startup.enabled": "Start mit Windows aktiviert.", "startup.disabled": "Start mit Windows deaktiviert.",
    "startup.enable_failed": "Start mit Windows konnte nicht aktiviert werden: {error}", "startup.disable_failed": "Start mit Windows konnte nicht deaktiviert werden: {error}",
})

_translations["es"].update({
    "common.all_files": "Todos los archivos", "common.close": "Cerrar", "common.off": "Desactivado", "common.on": "Activado",
    "shell.subtitle": "Control de enrutamiento de audio", "shell.searching": "Buscando monitores...",
    "nav.routes": "Rutas", "nav.integrations": "Integraciones", "nav.appearance": "Apariencia", "nav.settings": "Ajustes", "nav.diagnostics": "Diagnóstico",
    "page.routes.title": "Rutas de audio", "page.routes.description": "Envía cada entrada a la salida que quieras controlar.",
    "page.automations.description": "Crea pasos ordenados y elige cómo se ejecutan.",
    "page.integrations.description": "Configura conexiones compartidas para rutas y automatizaciones.",
    "page.appearance.description": "Elige cómo se muestran en pantalla los cambios de volumen.",
    "page.settings.description": "Administra el comportamiento de inicio y las copias de la configuración.",
    "settings.startup": "Inicio", "settings.start_with_windows": "Iniciar con Windows", "settings.startup_description": "Iniciar discretamente en el área de notificación al iniciar sesión.",
    "settings.configuration": "Configuración", "settings.configuration_description": "Exporta o importa una copia, o restaura los valores predeterminados incluidos.",
    "settings.export": "Exportar", "settings.import": "Importar", "settings.recent": "Recientes", "settings.restore_default": "Restaurar valores predeterminados",
    "config.file_type": "Configuración de FenSoundSwitch", "config.export_title": "Exportar configuración de FenSoundSwitch", "config.import_title": "Importar configuración de FenSoundSwitch",
    "config.restart_title": "¿Reiniciar FenSoundSwitch?", "config.restart_message": "Importar {name} cerrará y reiniciará FenSoundSwitch. ¿Continuar?",
    "config.none_available": "No hay ninguna configuración exportada disponible.", "config.default_missing": "No se encontró la configuración predeterminada: {path}.",
    "config.exported": "Configuración exportada a {path}.", "config.exported_history": "Configuración exportada a {path} y añadida al historial de importación.",
    "config.imported_restart": "Configuración importada desde {name}. Reiniciando FenSoundSwitch...",
    "diagnostics.window_title": "Registro de diagnóstico de FenSoundSwitch", "diagnostics.title": "Registro de diagnóstico", "diagnostics.description": "Eventos de la aplicación en tiempo real. Aquí nunca se escriben credenciales confidenciales.",
    "startup.enabled": "Inicio con Windows activado.", "startup.disabled": "Inicio con Windows desactivado.",
    "startup.enable_failed": "No se pudo activar el inicio con Windows: {error}", "startup.disable_failed": "No se pudo desactivar el inicio con Windows: {error}",
})

_translations["fr"].update({
    "common.all_files": "Tous les fichiers", "common.close": "Fermer", "common.off": "Désactivé", "common.on": "Activé",
    "shell.subtitle": "Contrôle du routage audio", "shell.searching": "Recherche des moniteurs...",
    "nav.routes": "Routes", "nav.integrations": "Intégrations", "nav.appearance": "Apparence", "nav.settings": "Paramètres", "nav.diagnostics": "Diagnostics",
    "page.routes.title": "Routes audio", "page.routes.description": "Envoyez chaque entrée vers la sortie que vous souhaitez contrôler.",
    "page.automations.description": "Créez des étapes ordonnées et choisissez leur mode d’exécution.",
    "page.integrations.description": "Configurez les connexions partagées utilisées par les routes et les automatisations.",
    "page.appearance.description": "Choisissez comment les changements de volume s’affichent à l’écran.",
    "page.settings.description": "Gérez le démarrage et les sauvegardes de configuration.",
    "settings.startup": "Démarrage", "settings.start_with_windows": "Démarrer avec Windows", "settings.startup_description": "Démarrer discrètement dans la zone de notification à l’ouverture de session.",
    "settings.configuration": "Configuration", "settings.configuration_description": "Exportez ou importez une sauvegarde, ou restaurez les paramètres par défaut inclus.",
    "settings.export": "Exporter", "settings.import": "Importer", "settings.recent": "Récentes", "settings.restore_default": "Restaurer les paramètres par défaut",
    "config.file_type": "Configuration FenSoundSwitch", "config.export_title": "Exporter la configuration FenSoundSwitch", "config.import_title": "Importer la configuration FenSoundSwitch",
    "config.restart_title": "Redémarrer FenSoundSwitch ?", "config.restart_message": "L’importation de {name} fermera et redémarrera FenSoundSwitch. Continuer ?",
    "config.none_available": "Aucune configuration exportée n’est disponible.", "config.default_missing": "Configuration par défaut introuvable : {path}.",
    "config.exported": "Configuration exportée vers {path}.", "config.exported_history": "Configuration exportée vers {path} et ajoutée à l’historique d’importation.",
    "config.imported_restart": "Configuration importée depuis {name}. Redémarrage de FenSoundSwitch...",
    "diagnostics.window_title": "Journal de diagnostic FenSoundSwitch", "diagnostics.title": "Journal de diagnostic", "diagnostics.description": "Événements en direct de l’application. Les identifiants sensibles ne sont jamais enregistrés ici.",
    "startup.enabled": "Démarrage avec Windows activé.", "startup.disabled": "Démarrage avec Windows désactivé.",
    "startup.enable_failed": "Impossible d’activer le démarrage avec Windows : {error}", "startup.disable_failed": "Impossible de désactiver le démarrage avec Windows : {error}",
})

_translations["it"].update({
    "common.all_files": "Tutti i file", "common.close": "Chiudi", "common.off": "Disattivato", "common.on": "Attivato",
    "shell.subtitle": "Controllo dell'instradamento audio", "shell.searching": "Ricerca dei monitor...",
    "nav.routes": "Percorsi", "nav.integrations": "Integrazioni", "nav.appearance": "Aspetto", "nav.settings": "Impostazioni", "nav.diagnostics": "Diagnostica",
    "page.routes.title": "Percorsi audio", "page.routes.description": "Invia ogni ingresso all'uscita che desideri controllare.",
    "page.automations.description": "Crea passaggi ordinati e scegli come eseguirli.",
    "page.integrations.description": "Configura le connessioni condivise usate da percorsi e automazioni.",
    "page.appearance.description": "Scegli come mostrare sullo schermo le variazioni del volume.",
    "page.settings.description": "Gestisci l'avvio e i backup della configurazione.",
    "settings.startup": "Avvio", "settings.start_with_windows": "Avvia con Windows", "settings.startup_description": "Avvia discretamente nell'area di notifica all'accesso.",
    "settings.configuration": "Configurazione", "settings.configuration_description": "Esporta o importa un backup oppure ripristina le impostazioni predefinite incluse.",
    "settings.export": "Esporta", "settings.import": "Importa", "settings.recent": "Recenti", "settings.restore_default": "Ripristina impostazioni predefinite",
    "config.file_type": "Configurazione FenSoundSwitch", "config.export_title": "Esporta configurazione FenSoundSwitch", "config.import_title": "Importa configurazione FenSoundSwitch",
    "config.restart_title": "Riavviare FenSoundSwitch?", "config.restart_message": "L'importazione di {name} chiuderà e riavvierà FenSoundSwitch. Continuare?",
    "config.none_available": "Non è disponibile alcuna configurazione esportata.", "config.default_missing": "Configurazione predefinita non trovata: {path}.",
    "config.exported": "Configurazione esportata in {path}.", "config.exported_history": "Configurazione esportata in {path} e aggiunta alla cronologia di importazione.",
    "config.imported_restart": "Configurazione importata da {name}. Riavvio di FenSoundSwitch...",
    "diagnostics.window_title": "Registro diagnostico di FenSoundSwitch", "diagnostics.title": "Registro diagnostico", "diagnostics.description": "Eventi dell'applicazione in tempo reale. Le credenziali sensibili non vengono mai registrate qui.",
    "startup.enabled": "Avvio con Windows attivato.", "startup.disabled": "Avvio con Windows disattivato.",
    "startup.enable_failed": "Impossibile attivare l'avvio con Windows: {error}", "startup.disable_failed": "Impossibile disattivare l'avvio con Windows: {error}",
})
_source_translations = {
    "de": {"Not yet read": "Noch nicht gelesen", "Provider is unavailable": "Anbieter ist nicht verfügbar", "Display-change protection is unavailable.": "Schutz vor Anzeigeänderungen ist nicht verfügbar.", "Selected monitor is unavailable.": "Der ausgewählte Monitor ist nicht verfügbar.", "The selected DDC monitor is unavailable or its identity is ambiguous.": "Der ausgewählte DDC-Monitor ist nicht verfügbar oder seine Identität ist mehrdeutig.", "Muted": "Stumm", "Unmuted": "Nicht stumm", "Playback": "Wiedergabe", "Voice output": "Sprachausgabe", "Input": "Eingabe", "Microphone": "Mikrofon", "Voice": "Sprache", "Headset": "Headset"},
    "es": {"Not yet read": "Aún no leído", "Provider is unavailable": "El proveedor no está disponible", "Display-change protection is unavailable.": "La protección ante cambios de pantalla no está disponible.", "Selected monitor is unavailable.": "El monitor seleccionado no está disponible.", "The selected DDC monitor is unavailable or its identity is ambiguous.": "El monitor DDC seleccionado no está disponible o su identidad es ambigua.", "Muted": "Silenciado", "Unmuted": "Con sonido", "Playback": "Reproducción", "Voice output": "Salida de voz", "Input": "Entrada", "Microphone": "Micrófono", "Voice": "Voz", "Headset": "Auriculares con micrófono"},
    "fr": {"Not yet read": "Pas encore lu", "Provider is unavailable": "Le fournisseur est indisponible", "Display-change protection is unavailable.": "La protection contre les changements d’affichage est indisponible.", "Selected monitor is unavailable.": "Le moniteur sélectionné est indisponible.", "The selected DDC monitor is unavailable or its identity is ambiguous.": "Le moniteur DDC sélectionné est indisponible ou son identité est ambiguë.", "Muted": "Muet", "Unmuted": "Son activé", "Playback": "Lecture", "Voice output": "Sortie vocale", "Input": "Entrée", "Microphone": "Microphone", "Voice": "Voix", "Headset": "Casque-micro"},
    "it": {"Not yet read": "Non ancora letto", "Provider is unavailable": "Il provider non è disponibile", "Display-change protection is unavailable.": "La protezione dai cambiamenti dello schermo non è disponibile.", "Selected monitor is unavailable.": "Il monitor selezionato non è disponibile.", "The selected DDC monitor is unavailable or its identity is ambiguous.": "Il monitor DDC selezionato non è disponibile o la sua identità è ambigua.", "Muted": "Audio disattivato", "Unmuted": "Audio attivo", "Playback": "Riproduzione", "Voice output": "Uscita voce", "Input": "Ingresso", "Microphone": "Microfono", "Voice": "Voce", "Headset": "Cuffie con microfono"},
}
_source_translations["de"].update({"Overlay test displayed.": "Einblendungstest angezeigt.", "Monitor discovery started.": "Monitorsuche gestartet.", "A DDC operation is already running.": "Ein DDC-Vorgang wird bereits ausgeführt.", "Monitor input step configured.": "Monitoreingangsschritt konfiguriert.", "Discord Developer Portal opened.": "Discord-Entwicklerportal geöffnet.", "Discord authorization started.": "Discord-Autorisierung gestartet.", "Discord authorization was reset.": "Discord-Autorisierung wurde zurückgesetzt."})
_source_translations["es"].update({"Overlay test displayed.": "Prueba de superposición mostrada.", "Monitor discovery started.": "Búsqueda de monitores iniciada.", "A DDC operation is already running.": "Ya hay una operación DDC en curso.", "Monitor input step configured.": "Paso de entrada de monitor configurado.", "Discord Developer Portal opened.": "Portal de desarrolladores de Discord abierto.", "Discord authorization started.": "Autorización de Discord iniciada.", "Discord authorization was reset.": "Se restableció la autorización de Discord."})
_source_translations["fr"].update({"Overlay test displayed.": "Test de superposition affiché.", "Monitor discovery started.": "Recherche des moniteurs lancée.", "A DDC operation is already running.": "Une opération DDC est déjà en cours.", "Monitor input step configured.": "Étape d’entrée moniteur configurée.", "Discord Developer Portal opened.": "Portail développeur Discord ouvert.", "Discord authorization started.": "Autorisation Discord lancée.", "Discord authorization was reset.": "L’autorisation Discord a été réinitialisée."})
_source_translations["it"].update({"Overlay test displayed.": "Test overlay visualizzato.", "Monitor discovery started.": "Ricerca monitor avviata.", "A DDC operation is already running.": "È già in corso un’operazione DDC.", "Monitor input step configured.": "Passaggio ingresso monitor configurato.", "Discord Developer Portal opened.": "Portale sviluppatori Discord aperto.", "Discord authorization started.": "Autorizzazione Discord avviata.", "Discord authorization was reset.": "L’autorizzazione Discord è stata reimpostata."})
_source_translations["de"].update({"Ready": "Bereit", "Disabled": "Deaktiviert", "Discovering DDC monitor inputs": "DDC-Monitoreingänge werden gesucht", "Settings were invalid; keep-alive is disabled": "Die Einstellungen waren ungültig; die Aktivhaltung ist deaktiviert", "MQTT publish step configured.": "MQTT-Veröffentlichungsschritt konfiguriert.", "Monitor refresh started.": "Monitoraktualisierung gestartet.", "Power-plan refresh started.": "Aktualisierung der Energiesparpläne gestartet.", "OBS request timed out.": "Zeitüberschreitung der OBS-Anfrage.", "OBS request timed out or the connection failed.": "Zeitüberschreitung der OBS-Anfrage oder Verbindungsfehler.", "Active": "Aktiv"})
_source_translations["es"].update({"Ready": "Listo", "Disabled": "Desactivado", "Discovering DDC monitor inputs": "Buscando entradas de monitor DDC", "Settings were invalid; keep-alive is disabled": "Los ajustes no eran válidos; el mantenimiento está desactivado", "MQTT publish step configured.": "Paso de publicación MQTT configurado.", "Monitor refresh started.": "Actualización de monitores iniciada.", "Power-plan refresh started.": "Actualización de planes de energía iniciada.", "OBS request timed out.": "Se agotó el tiempo de la solicitud OBS.", "OBS request timed out or the connection failed.": "Se agotó el tiempo de la solicitud OBS o falló la conexión.", "Active": "Activo"})
_source_translations["fr"].update({"Ready": "Prêt", "Disabled": "Désactivé", "Discovering DDC monitor inputs": "Recherche des entrées moniteur DDC", "Settings were invalid; keep-alive is disabled": "Les paramètres étaient invalides ; le maintien est désactivé", "MQTT publish step configured.": "Étape de publication MQTT configurée.", "Monitor refresh started.": "Actualisation des moniteurs lancée.", "Power-plan refresh started.": "Actualisation des modes d’alimentation lancée.", "OBS request timed out.": "Délai de la requête OBS dépassé.", "OBS request timed out or the connection failed.": "Délai de la requête OBS dépassé ou échec de la connexion.", "Active": "Actif"})
_source_translations["it"].update({"Ready": "Pronto", "Disabled": "Disabilitato", "Discovering DDC monitor inputs": "Ricerca ingressi monitor DDC", "Settings were invalid; keep-alive is disabled": "Le impostazioni non erano valide; il mantenimento è disabilitato", "MQTT publish step configured.": "Passaggio di pubblicazione MQTT configurato.", "Monitor refresh started.": "Aggiornamento monitor avviato.", "Power-plan refresh started.": "Aggiornamento piani di alimentazione avviato.", "OBS request timed out.": "Timeout della richiesta OBS.", "OBS request timed out or the connection failed.": "Timeout della richiesta OBS o connessione non riuscita.", "Active": "Attivo"})
for _locale, _value in {"de": "Aktiv nach Mausbewegung", "es": "Activo tras mover el ratón", "fr": "Actif après mouvement de la souris", "it": "Attivo dopo il movimento del mouse"}.items():
    _source_translations[_locale]["Active after mouse movement"] = _value
for _locale, _values in {
    "de": {"Not configured": "Nicht konfiguriert", "Configuration unavailable": "Konfiguration nicht verfügbar", "Unavailable": "Nicht verfügbar", "Monitor discovery could not be started.": "Die Monitorsuche konnte nicht gestartet werden.", "No configurable DDC monitors were found.": "Es wurden keine konfigurierbaren DDC-Monitore gefunden."},
    "es": {"Not configured": "Sin configurar", "Configuration unavailable": "Configuración no disponible", "Unavailable": "No disponible", "Monitor discovery could not be started.": "No se pudo iniciar la búsqueda de monitores.", "No configurable DDC monitors were found.": "No se encontraron monitores DDC configurables."},
    "fr": {"Not configured": "Non configuré", "Configuration unavailable": "Configuration indisponible", "Unavailable": "Indisponible", "Monitor discovery could not be started.": "La recherche des moniteurs n’a pas pu démarrer.", "No configurable DDC monitors were found.": "Aucun moniteur DDC configurable n’a été trouvé."},
    "it": {"Not configured": "Non configurato", "Configuration unavailable": "Configurazione non disponibile", "Unavailable": "Non disponibile", "Monitor discovery could not be started.": "Impossibile avviare la ricerca dei monitor.", "No configurable DDC monitors were found.": "Non sono stati trovati monitor DDC configurabili."},
}.items():
    _source_translations[_locale].update(_values)

# Error wrappers are matched before ordinary source translation so details from
# devices, sockets, Windows, Discord, and third-party libraries stay opaque.
_opaque_error_templates = (
    (
        re.compile(r"^Could not communicate with the configured AVR: (.*)$"),
        {
            "de": "Kommunikation mit dem konfigurierten AVR fehlgeschlagen: {detail}",
            "es": "No se pudo comunicar con el AVR configurado: {detail}",
            "fr": "Impossible de communiquer avec l’AVR configuré : {detail}",
            "it": "Impossibile comunicare con l’AVR configurato: {detail}",
        },
    ),
    (
        re.compile(r"^Could not communicate with the configured Onkyo receiver: (.*)$"),
        {
            "de": "Kommunikation mit dem konfigurierten Onkyo-Receiver fehlgeschlagen: {detail}",
            "es": "No se pudo comunicar con el receptor Onkyo configurado: {detail}",
            "fr": "Impossible de communiquer avec le récepteur Onkyo configuré : {detail}",
            "it": "Impossibile comunicare con il ricevitore Onkyo configurato: {detail}",
        },
    ),
    (
        re.compile(r"^Could not communicate with the configured Pioneer/Elite receiver: (.*)$"),
        {
            "de": "Kommunikation mit dem konfigurierten Pioneer/Elite-Receiver fehlgeschlagen: {detail}",
            "es": "No se pudo comunicar con el receptor Pioneer/Elite configurado: {detail}",
            "fr": "Impossible de communiquer avec le récepteur Pioneer/Elite configuré : {detail}",
            "it": "Impossibile comunicare con il ricevitore Pioneer/Elite configurato: {detail}",
        },
    ),
    (
        re.compile(r"^Could not communicate with the configured Sony receiver: (.*)$"),
        {
            "de": "Kommunikation mit dem konfigurierten Sony-Receiver fehlgeschlagen: {detail}",
            "es": "No se pudo comunicar con el receptor Sony configurado: {detail}",
            "fr": "Impossible de communiquer avec le récepteur Sony configuré : {detail}",
            "it": "Impossibile comunicare con il ricevitore Sony configurato: {detail}",
        },
    ),
    (
        re.compile(r"^Could not communicate with the configured Yamaha receiver: (.*)$"),
        {
            "de": "Kommunikation mit dem konfigurierten Yamaha-Receiver fehlgeschlagen: {detail}",
            "es": "No se pudo comunicar con el receptor Yamaha configurado: {detail}",
            "fr": "Impossible de communiquer avec le récepteur Yamaha configuré : {detail}",
            "it": "Impossibile comunicare con il ricevitore Yamaha configurato: {detail}",
        },
    ),
    (
        re.compile(r"^Discord OAuth request failed with HTTP (.*)$"),
        {
            "de": "Discord-OAuth-Anfrage mit HTTP {detail} fehlgeschlagen",
            "es": "La solicitud OAuth de Discord falló con HTTP {detail}",
            "fr": "La requête OAuth Discord a échoué avec HTTP {detail}",
            "it": "La richiesta OAuth Discord non è riuscita con HTTP {detail}",
        },
    ),
    (
        re.compile(r"^Discord returned no settings for (.*)$"),
        {
            "de": "Discord hat keine Einstellungen für {detail} zurückgegeben",
            "es": "Discord no devolvió ajustes para {detail}",
            "fr": "Discord n’a renvoyé aucun paramètre pour {detail}",
            "it": "Discord non ha restituito impostazioni per {detail}",
        },
    ),
    (
        re.compile(r"^Discord is not running or no local RPC pipe is available(?:: (.*))?$"),
        {
            "de": "Discord wird nicht ausgeführt oder es ist keine lokale RPC-Pipe verfügbar{suffix}",
            "es": "Discord no se está ejecutando o no hay ninguna canalización RPC local disponible{suffix}",
            "fr": "Discord n’est pas en cours d’exécution ou aucun canal RPC local n’est disponible{suffix}",
            "it": "Discord non è in esecuzione o non è disponibile alcuna pipe RPC locale{suffix}",
        },
    ),
)

_opaque_plugin_wrappers = (
    (
        re.compile(r"^Could not communicate with the configured Sonos speaker: (.*)$"),
        {"de": "Kommunikation mit dem konfigurierten Sonos-Lautsprecher fehlgeschlagen: {detail}", "es": "No se pudo comunicar con el altavoz Sonos configurado: {detail}", "fr": "Impossible de communiquer avec l’enceinte Sonos configurée : {detail}", "it": "Impossibile comunicare con l’altoparlante Sonos configurato: {detail}"},
    ),
    (
        re.compile(r"^Could not load the documented Voicemeeter Remote DLL: (.*)$"),
        {"de": "Die dokumentierte Voicemeeter Remote-DLL konnte nicht geladen werden: {detail}", "es": "No se pudo cargar la DLL documentada de Voicemeeter Remote: {detail}", "fr": "Impossible de charger la DLL Voicemeeter Remote documentée : {detail}", "it": "Impossibile caricare la DLL Voicemeeter Remote documentata: {detail}"},
    ),
    (
        re.compile(r"^Could not communicate with the configured HTTP endpoint: (.*)$"),
        {"de": "Kommunikation mit dem konfigurierten HTTP-Endpunkt fehlgeschlagen: {detail}", "es": "No se pudo comunicar con el extremo HTTP configurado: {detail}", "fr": "Impossible de communiquer avec le point HTTP configuré : {detail}", "it": "Impossibile comunicare con l’endpoint HTTP configurato: {detail}"},
    ),
    (
        re.compile(r"^OBS rejected ([^:]+)(?:: (.*?))?( \(code -?\d+\))?\.$"),
        {"de": "OBS hat {value} abgelehnt{detail}{code}.", "es": "OBS rechazó {value}{detail}{code}.", "fr": "OBS a rejeté {value}{detail}{code}.", "it": "OBS ha rifiutato {value}{detail}{code}."},
    ),
    (
        re.compile(r"^Failed to set monitor (.*?): (.*)$"),
        {"de": "Monitor {value} konnte nicht eingestellt werden: {detail}", "es": "No se pudo configurar el monitor {value}: {detail}", "fr": "Impossible de régler le moniteur {value} : {detail}", "it": "Impossibile impostare il monitor {value}: {detail}"},
    ),
    (
        re.compile(r"^(HTTP request failed with status|MQTT broker connection failed with result|MQTT publish failed with result|Voicemeeter Remote login failed with status) (.*?)(\.)?$"),
        {
            "de": "{prefix} {detail}{period}", "es": "{prefix} {detail}{period}",
            "fr": "{prefix} {detail}{period}", "it": "{prefix} {detail}{period}",
        },
    ),
    (
        re.compile(r"^(Voicemeeter rejected parameter) (.*?) (with status) (.*?)(\.)?$"),
        {
            "de": "Voicemeeter hat Parameter {value} mit Status {detail} abgelehnt{period}",
            "es": "Voicemeeter rechazó el parámetro {value} con estado {detail}{period}",
            "fr": "Voicemeeter a rejeté le paramètre {value} avec l’état {detail}{period}",
            "it": "Voicemeeter ha rifiutato il parametro {value} con stato {detail}{period}",
        },
    ),
    (
        re.compile(r"^(The speaker response omitted) (.*?)(\.)?$"),
        {"de": "In der Lautsprecherantwort fehlt {detail}{period}", "es": "La respuesta del altavoz omitió {detail}{period}", "fr": "La réponse de l’enceinte ne contient pas {detail}{period}", "it": "La risposta dell’altoparlante non contiene {detail}{period}"},
    ),
)

_result_wrapper_prefixes = {
    "de": {"HTTP request failed with status": "HTTP-Anfrage fehlgeschlagen, Status", "MQTT broker connection failed with result": "MQTT-Brokerverbindung fehlgeschlagen, Ergebnis", "MQTT publish failed with result": "MQTT-Veröffentlichung fehlgeschlagen, Ergebnis", "Voicemeeter Remote login failed with status": "Voicemeeter Remote-Anmeldung fehlgeschlagen, Status"},
    "es": {"HTTP request failed with status": "La solicitud HTTP falló con estado", "MQTT broker connection failed with result": "La conexión con el bróker MQTT falló con resultado", "MQTT publish failed with result": "La publicación MQTT falló con resultado", "Voicemeeter Remote login failed with status": "El inicio de sesión de Voicemeeter Remote falló con estado"},
    "fr": {"HTTP request failed with status": "La requête HTTP a échoué avec l’état", "MQTT broker connection failed with result": "La connexion au broker MQTT a échoué avec le résultat", "MQTT publish failed with result": "La publication MQTT a échoué avec le résultat", "Voicemeeter Remote login failed with status": "La connexion Voicemeeter Remote a échoué avec l’état"},
    "it": {"HTTP request failed with status": "La richiesta HTTP non è riuscita con stato", "MQTT broker connection failed with result": "La connessione al broker MQTT non è riuscita con risultato", "MQTT publish failed with result": "La pubblicazione MQTT non è riuscita con risultato", "Voicemeeter Remote login failed with status": "L’accesso a Voicemeeter Remote non è riuscito con stato"},
}

_dynamic_validation_suffixes = {
    "de": {"contains a duplicate key.": "enthält einen doppelten Schlüssel.", "contains an invalid TCP port.": "enthält einen ungültigen TCP-Port.", "contains an invalid character.": "enthält ein ungültiges Zeichen.", "is malformed.": "ist fehlerhaft.", "must be valid finite JSON.": "muss gültiges endliches JSON sein.", "must not contain URL credentials.": "darf keine URL-Anmeldedaten enthalten.", "must not contain a fragment.": "darf kein Fragment enthalten.", "must use http:// or https:// and include a host.": "muss http:// oder https:// verwenden und einen Host enthalten."},
    "es": {"contains a duplicate key.": "contiene una clave duplicada.", "contains an invalid TCP port.": "contiene un puerto TCP no válido.", "contains an invalid character.": "contiene un carácter no válido.", "is malformed.": "tiene un formato incorrecto.", "must be valid finite JSON.": "debe ser JSON finito válido.", "must not contain URL credentials.": "no debe contener credenciales en la URL.", "must not contain a fragment.": "no debe contener un fragmento.", "must use http:// or https:// and include a host.": "debe usar http:// o https:// e incluir un host."},
    "fr": {"contains a duplicate key.": "contient une clé en double.", "contains an invalid TCP port.": "contient un port TCP invalide.", "contains an invalid character.": "contient un caractère invalide.", "is malformed.": "est mal formé.", "must be valid finite JSON.": "doit être un JSON fini valide.", "must not contain URL credentials.": "ne doit contenir aucun identifiant dans l’URL.", "must not contain a fragment.": "ne doit contenir aucun fragment.", "must use http:// or https:// and include a host.": "doit utiliser http:// ou https:// et inclure un hôte."},
    "it": {"contains a duplicate key.": "contiene una chiave duplicata.", "contains an invalid TCP port.": "contiene una porta TCP non valida.", "contains an invalid character.": "contiene un carattere non valido.", "is malformed.": "non è valido.", "must be valid finite JSON.": "deve essere JSON finito valido.", "must not contain URL credentials.": "non deve contenere credenziali nell’URL.", "must not contain a fragment.": "non deve contenere un frammento.", "must use http:// or https:// and include a host.": "deve usare http:// o https:// e includere un host."},
}

_http_error_translations = {
    "de": {"HTTP header is controlled by the application and cannot be overridden.": "Der HTTP-Header wird von der Anwendung gesteuert und kann nicht überschrieben werden.", "HTTP headers may contain at most 32 fields.": "HTTP-Header dürfen höchstens 32 Felder enthalten.", "HTTP headers must contain valid finite JSON text.": "HTTP-Header müssen gültigen endlichen JSON-Text enthalten.", "HTTP read method must be GET or POST.": "Die HTTP-Lesemethode muss GET oder POST sein.", "HTTP request body must be valid UTF-8 text.": "Der HTTP-Anfragetext muss gültiger UTF-8-Text sein.", "HTTP request body must contain exactly one {volume} placeholder.": "Der HTTP-Anfragetext muss genau einen Platzhalter {volume} enthalten.", "HTTP response field must be a non-empty JSON key of at most 128 characters.": "Das HTTP-Antwortfeld muss ein nicht leerer JSON-Schlüssel mit höchstens 128 Zeichen sein.", "HTTP write method must be POST, PUT, or PATCH.": "Die HTTP-Schreibmethode muss POST, PUT oder PATCH sein.", "Non-finite JSON numbers are not supported.": "Nicht endliche JSON-Zahlen werden nicht unterstützt.", "The HTTP endpoint response must be a JSON object.": "Die Antwort des HTTP-Endpunkts muss ein JSON-Objekt sein."},
    "es": {"HTTP header is controlled by the application and cannot be overridden.": "La aplicación controla la cabecera HTTP y no se puede reemplazar.", "HTTP headers may contain at most 32 fields.": "Las cabeceras HTTP pueden contener como máximo 32 campos.", "HTTP headers must contain valid finite JSON text.": "Las cabeceras HTTP deben contener texto JSON finito válido.", "HTTP read method must be GET or POST.": "El método de lectura HTTP debe ser GET o POST.", "HTTP request body must be valid UTF-8 text.": "El cuerpo de la solicitud HTTP debe ser texto UTF-8 válido.", "HTTP request body must contain exactly one {volume} placeholder.": "El cuerpo de la solicitud HTTP debe contener exactamente un marcador {volume}.", "HTTP response field must be a non-empty JSON key of at most 128 characters.": "El campo de respuesta HTTP debe ser una clave JSON no vacía de hasta 128 caracteres.", "HTTP write method must be POST, PUT, or PATCH.": "El método de escritura HTTP debe ser POST, PUT o PATCH.", "Non-finite JSON numbers are not supported.": "No se admiten números JSON no finitos.", "The HTTP endpoint response must be a JSON object.": "La respuesta del extremo HTTP debe ser un objeto JSON."},
    "fr": {"HTTP header is controlled by the application and cannot be overridden.": "L’en-tête HTTP est contrôlé par l’application et ne peut pas être remplacé.", "HTTP headers may contain at most 32 fields.": "Les en-têtes HTTP peuvent contenir au maximum 32 champs.", "HTTP headers must contain valid finite JSON text.": "Les en-têtes HTTP doivent contenir un texte JSON fini valide.", "HTTP read method must be GET or POST.": "La méthode de lecture HTTP doit être GET ou POST.", "HTTP request body must be valid UTF-8 text.": "Le corps de la requête HTTP doit être un texte UTF-8 valide.", "HTTP request body must contain exactly one {volume} placeholder.": "Le corps de la requête HTTP doit contenir exactement un espace réservé {volume}.", "HTTP response field must be a non-empty JSON key of at most 128 characters.": "Le champ de réponse HTTP doit être une clé JSON non vide de 128 caractères maximum.", "HTTP write method must be POST, PUT, or PATCH.": "La méthode d’écriture HTTP doit être POST, PUT ou PATCH.", "Non-finite JSON numbers are not supported.": "Les nombres JSON non finis ne sont pas pris en charge.", "The HTTP endpoint response must be a JSON object.": "La réponse du point HTTP doit être un objet JSON."},
    "it": {"HTTP header is controlled by the application and cannot be overridden.": "L’intestazione HTTP è controllata dall’applicazione e non può essere sostituita.", "HTTP headers may contain at most 32 fields.": "Le intestazioni HTTP possono contenere al massimo 32 campi.", "HTTP headers must contain valid finite JSON text.": "Le intestazioni HTTP devono contenere testo JSON finito valido.", "HTTP read method must be GET or POST.": "Il metodo di lettura HTTP deve essere GET o POST.", "HTTP request body must be valid UTF-8 text.": "Il corpo della richiesta HTTP deve essere testo UTF-8 valido.", "HTTP request body must contain exactly one {volume} placeholder.": "Il corpo della richiesta HTTP deve contenere esattamente un segnaposto {volume}.", "HTTP response field must be a non-empty JSON key of at most 128 characters.": "Il campo della risposta HTTP deve essere una chiave JSON non vuota di massimo 128 caratteri.", "HTTP write method must be POST, PUT, or PATCH.": "Il metodo di scrittura HTTP deve essere POST, PUT o PATCH.", "Non-finite JSON numbers are not supported.": "I numeri JSON non finiti non sono supportati.", "The HTTP endpoint response must be a JSON object.": "La risposta dell’endpoint HTTP deve essere un oggetto JSON."},
}

# These ordered substitutions cover deterministic bundled-plugin failures that
# do not carry opaque data. Longer phrases run first. Product names, protocol
# identifiers, field names, numeric ranges, and action IDs are deliberately not
# substitutions.
_error_phrases = {
    "de": {
        "The receiver returned": "Der Receiver hat",
        "The receiver did not return": "Der Receiver hat keine",
        "The receiver did not confirm": "Der Receiver hat nicht bestätigt:",
        "The receiver closed": "Der Receiver hat geschlossen:",
        "Timed out waiting for": "Zeitüberschreitung beim Warten auf",
        "Could not reach": "Nicht erreichbar:",
        "must be an object": "müssen ein Objekt sein",
        "must be true or false": "müssen wahr oder falsch sein",
        "is not initialized": "ist nicht initialisiert",
        "has not been initialized": "wurde nicht initialisiert",
        "is shutting down": "wird beendet",
        "is unavailable or its identity is ambiguous": "ist nicht verfügbar oder seine Identität ist mehrdeutig",
        "is unavailable or still in use": "ist nicht verfügbar oder wird noch verwendet",
        "is not currently available": "ist derzeit nicht verfügbar",
        "does not accept parameters": "akzeptiert keine Parameter",
        "does not expose shortcut actions": "stellt keine Tastenkürzelaktionen bereit",
        "has no shortcut actions": "hat keine Tastenkürzelaktionen",
        "has no actions": "hat keine Aktionen",
        "must be non-empty bytes": "muss eine nicht leere Bytefolge sein",
        "must be from": "muss zwischen",
        "is required": "ist erforderlich",
        "is too long": "ist zu lang",
        "must be text": "muss Text sein",
        "must be unique": "müssen eindeutig sein",
        "must match": "muss entsprechen",
        "may contain only": "darf nur Folgendes enthalten:",
        "contains unknown values": "enthält unbekannte Werte",
        "has unknown settings": "enthält unbekannte Einstellungen",
        "settings are invalid": "Einstellungen sind ungültig",
        "setting must be": "Einstellung muss sein:",
        "parameters must be": "Parameter müssen sein:",
        "is invalid": "ist ungültig",
        "are invalid": "sind ungültig",
        "is not installed": "ist nicht installiert",
        "does not exist": "ist nicht vorhanden",
        "must differ": "müssen unterschiedlich sein",
        "is route scoped": "ist auf die Route beschränkt",
        "requires": "erfordert",
        "Choose a": "Wählen Sie eine",
        "Choose": "Wählen Sie",
        "Select a": "Wählen Sie einen",
        "Select": "Wählen Sie",
        "Configure a": "Konfigurieren Sie einen",
        "Configure an": "Konfigurieren Sie einen",
        "Configure this": "Konfigurieren Sie diesen",
        "Enter a valid": "Geben Sie gültige Werte ein für",
        "Unknown": "Unbekannte Aktion:",
        "Another": "Ein weiterer",
        "At least": "Mindestens",
        "Wait for": "Warten Sie auf",
        "Refusing to save": "Speichern verweigert:",
        "The current": "Der aktuelle",
        "The selected": "Der ausgewählte",
        "The saved": "Die gespeicherte",
        "The requested": "Der angeforderte",
        "The main-zone": "Die Hauptzonen-",
        "The AVR": "Der AVR",
        "Audio endpoint": "Audioendpunkt",
        "Audio keep-alive": "Audio-Aktivhaltung",
        "Keyboard": "Tastatur",
        "Mouse activity interval": "Mausaktivitätsintervall",
        "Overlay plugin": "Einblendungs-Plugin",
        "route parameters": "Routenparameter",
        "shutdown timeout cannot be negative": "Zeitüberschreitung beim Beenden darf nicht negativ sein",
        "returned": "zurückgegeben",
        "unsupported": "nicht unterstützte",
        "unexpected": "unerwartete",
        "invalid": "ungültige",
        "malformed": "fehlerhafte",
        "oversized": "zu große",
        "no alternative": "kein alternatives",
        "no output-device settings": "keine Ausgabegeräteeinstellungen",
        "no OAuth": "keine OAuth-",
        "no RPC": "keinen RPC-",
        "did not confirm": "hat nicht bestätigt",
        "not completed": "nicht abgeschlossen",
        "not configured": "nicht konfiguriert",
        "connection was closed": "Verbindung wurde geschlossen",
        "closed the RPC connection": "hat die RPC-Verbindung geschlossen",
        "too large to save": "ist zu groß zum Speichern",
        "outside": "außerhalb",
        "before": "bevor",
        "only": "nur",
    },
    "es": {
        "The receiver returned": "El receptor devolvió",
        "The receiver did not return": "El receptor no devolvió",
        "The receiver did not confirm": "El receptor no confirmó",
        "The receiver closed": "El receptor cerró",
        "Timed out waiting for": "Se agotó el tiempo esperando",
        "Could not reach": "No se pudo acceder a",
        "must be an object": "debe ser un objeto",
        "must be true or false": "debe ser verdadero o falso",
        "is not initialized": "no está inicializado",
        "has not been initialized": "no se ha inicializado",
        "is shutting down": "se está cerrando",
        "is unavailable or its identity is ambiguous": "no está disponible o su identidad es ambigua",
        "is unavailable or still in use": "no está disponible o todavía está en uso",
        "is not currently available": "no está disponible actualmente",
        "does not accept parameters": "no acepta parámetros",
        "does not expose shortcut actions": "no ofrece acciones de atajo",
        "has no shortcut actions": "no tiene acciones de atajo",
        "has no actions": "no tiene acciones",
        "must be non-empty bytes": "debe ser una secuencia de bytes no vacía",
        "must be from": "debe estar entre",
        "is required": "es obligatorio",
        "is too long": "es demasiado largo",
        "must be text": "debe ser texto",
        "must be unique": "deben ser únicos",
        "must match": "debe coincidir con",
        "may contain only": "solo puede contener",
        "contains unknown values": "contiene valores desconocidos",
        "has unknown settings": "contiene ajustes desconocidos",
        "settings are invalid": "tiene ajustes no válidos",
        "setting must be": "debe ser",
        "parameters must be": "los parámetros deben ser",
        "is invalid": "no es válido",
        "are invalid": "no son válidos",
        "is not installed": "no está instalado",
        "does not exist": "no existe",
        "must differ": "deben ser diferentes",
        "is route scoped": "está limitado a la ruta",
        "requires": "requiere",
        "Choose a": "Elija una",
        "Choose": "Elija",
        "Select a": "Seleccione un",
        "Select": "Seleccione",
        "Configure a": "Configure un",
        "Configure an": "Configure un",
        "Configure this": "Configure este",
        "Enter a valid": "Introduzca valores válidos para",
        "Unknown": "Acción desconocida:",
        "Another": "Otra",
        "At least": "Al menos",
        "Wait for": "Espere a que termine",
        "Refusing to save": "Se rechazó guardar",
        "The current": "El actual",
        "The selected": "El seleccionado",
        "The saved": "La concesión guardada",
        "The requested": "El volumen solicitado",
        "The main-zone": "El volumen de zona principal",
        "The AVR": "El AVR",
        "Audio endpoint": "El flujo del extremo de audio",
        "Audio keep-alive": "El mantenimiento de audio",
        "Keyboard": "El teclado",
        "Mouse activity interval": "El intervalo de actividad del ratón",
        "Overlay plugin": "El plugin de superposición",
        "route parameters": "los parámetros de ruta",
        "shutdown timeout cannot be negative": "el tiempo de espera de cierre no puede ser negativo",
        "returned": "devolvió",
        "unsupported": "no compatible",
        "unexpected": "inesperada",
        "invalid": "no válido",
        "malformed": "con formato incorrecto",
        "oversized": "demasiado grande",
        "no alternative": "ningún dispositivo alternativo",
        "no output-device settings": "ningún ajuste de dispositivo de salida",
        "no OAuth": "ningún dato OAuth de",
        "no RPC": "ningún código RPC de",
        "did not confirm": "no confirmó",
        "not completed": "no se ha completado",
        "not configured": "no está configurada",
        "connection was closed": "se cerró la conexión",
        "closed the RPC connection": "cerró la conexión RPC",
        "too large to save": "es demasiado grande para guardarla",
        "outside": "fuera de",
        "before": "antes de",
        "only": "solo",
    },
    "fr": {
        "The receiver returned": "Le récepteur a renvoyé",
        "The receiver did not return": "Le récepteur n’a pas renvoyé",
        "The receiver did not confirm": "Le récepteur n’a pas confirmé",
        "The receiver closed": "Le récepteur a fermé",
        "Timed out waiting for": "Délai dépassé en attendant",
        "Could not reach": "Impossible de joindre",
        "must be an object": "doit être un objet",
        "must be true or false": "doit être vrai ou faux",
        "is not initialized": "n’est pas initialisé",
        "has not been initialized": "n’a pas été initialisé",
        "is shutting down": "est en cours d’arrêt",
        "is unavailable or its identity is ambiguous": "est indisponible ou son identité est ambiguë",
        "is unavailable or still in use": "est indisponible ou encore utilisé",
        "is not currently available": "n’est pas disponible actuellement",
        "does not accept parameters": "n’accepte aucun paramètre",
        "does not expose shortcut actions": "n’expose aucune action de raccourci",
        "has no shortcut actions": "n’a aucune action de raccourci",
        "has no actions": "n’a aucune action",
        "must be non-empty bytes": "doit être une séquence d’octets non vide",
        "must be from": "doit être compris entre",
        "is required": "est obligatoire",
        "is too long": "est trop long",
        "must be text": "doit être du texte",
        "must be unique": "doivent être uniques",
        "must match": "doit correspondre à",
        "may contain only": "ne peut contenir que",
        "contains unknown values": "contient des valeurs inconnues",
        "has unknown settings": "contient des paramètres inconnus",
        "settings are invalid": "a des paramètres invalides",
        "setting must be": "doit être",
        "parameters must be": "les paramètres doivent être",
        "is invalid": "est invalide",
        "are invalid": "sont invalides",
        "is not installed": "n’est pas installé",
        "does not exist": "n’existe pas",
        "must differ": "doivent être différentes",
        "is route scoped": "est limité à la route",
        "requires": "nécessite",
        "Choose a": "Choisissez une",
        "Choose": "Choisissez",
        "Select a": "Sélectionnez un",
        "Select": "Sélectionnez",
        "Configure a": "Configurez un",
        "Configure an": "Configurez un",
        "Configure this": "Configurez cette",
        "Enter a valid": "Saisissez des valeurs valides pour",
        "Unknown": "Action inconnue :",
        "Another": "Une autre",
        "At least": "Au moins",
        "Wait for": "Attendez la fin de",
        "Refusing to save": "Enregistrement refusé :",
        "The current": "L’actuel",
        "The selected": "Le sélectionné",
        "The saved": "L’autorisation enregistrée",
        "The requested": "Le volume demandé",
        "The main-zone": "Le volume de zone principale",
        "The AVR": "L’AVR",
        "Audio endpoint": "Le flux du point audio",
        "Audio keep-alive": "Le maintien audio",
        "Keyboard": "Le clavier",
        "Mouse activity interval": "L’intervalle d’activité de la souris",
        "Overlay plugin": "Le plugin de superposition",
        "route parameters": "les paramètres de route",
        "shutdown timeout cannot be negative": "le délai d’arrêt ne peut pas être négatif",
        "returned": "a renvoyé",
        "unsupported": "non pris en charge",
        "unexpected": "inattendue",
        "invalid": "invalide",
        "malformed": "mal formé",
        "oversized": "trop volumineux",
        "no alternative": "aucun périphérique alternatif",
        "no output-device settings": "aucun paramètre de périphérique de sortie",
        "no OAuth": "aucune donnée OAuth de",
        "no RPC": "aucun code RPC de",
        "did not confirm": "n’a pas confirmé",
        "not completed": "n’est pas terminée",
        "not configured": "n’est pas configurée",
        "connection was closed": "a été fermée",
        "closed the RPC connection": "a fermé la connexion RPC",
        "too large to save": "est trop volumineuse pour être enregistrée",
        "outside": "hors de",
        "before": "avant de",
        "only": "uniquement",
    },
    "it": {
        "The receiver returned": "Il ricevitore ha restituito",
        "The receiver did not return": "Il ricevitore non ha restituito",
        "The receiver did not confirm": "Il ricevitore non ha confermato",
        "The receiver closed": "Il ricevitore ha chiuso",
        "Timed out waiting for": "Tempo scaduto durante l’attesa di",
        "Could not reach": "Impossibile raggiungere",
        "must be an object": "deve essere un oggetto",
        "must be true or false": "deve essere vero o falso",
        "is not initialized": "non è inizializzato",
        "has not been initialized": "non è stato inizializzato",
        "is shutting down": "è in fase di arresto",
        "is unavailable or its identity is ambiguous": "non è disponibile o la sua identità è ambigua",
        "is unavailable or still in use": "non è disponibile o è ancora in uso",
        "is not currently available": "non è attualmente disponibile",
        "does not accept parameters": "non accetta parametri",
        "does not expose shortcut actions": "non espone azioni di scelta rapida",
        "has no shortcut actions": "non ha azioni di scelta rapida",
        "has no actions": "non ha azioni",
        "must be non-empty bytes": "deve essere una sequenza di byte non vuota",
        "must be from": "deve essere compreso tra",
        "is required": "è obbligatorio",
        "is too long": "è troppo lungo",
        "must be text": "deve essere testo",
        "must be unique": "devono essere univoci",
        "must match": "deve corrispondere a",
        "may contain only": "può contenere solo",
        "contains unknown values": "contiene valori sconosciuti",
        "has unknown settings": "contiene impostazioni sconosciute",
        "settings are invalid": "ha impostazioni non valide",
        "setting must be": "deve essere",
        "parameters must be": "i parametri devono essere",
        "is invalid": "non è valido",
        "are invalid": "non sono validi",
        "is not installed": "non è installato",
        "does not exist": "non esiste",
        "must differ": "devono essere diversi",
        "is route scoped": "è limitato al percorso",
        "requires": "richiede",
        "Choose a": "Scegli un",
        "Choose": "Scegli",
        "Select a": "Seleziona un",
        "Select": "Seleziona",
        "Configure a": "Configura un",
        "Configure an": "Configura un",
        "Configure this": "Configura questo",
        "Enter a valid": "Inserisci valori validi per",
        "Unknown": "Azione sconosciuta:",
        "Another": "Un’altra",
        "At least": "Almeno",
        "Wait for": "Attendi il completamento di",
        "Refusing to save": "Salvataggio rifiutato:",
        "The current": "L’attuale",
        "The selected": "Il selezionato",
        "The saved": "L’autorizzazione salvata",
        "The requested": "Il volume richiesto",
        "The main-zone": "Il volume della zona principale",
        "The AVR": "L’AVR",
        "Audio endpoint": "Il flusso dell’endpoint audio",
        "Audio keep-alive": "Il mantenimento audio",
        "Keyboard": "La tastiera",
        "Mouse activity interval": "L’intervallo di attività del mouse",
        "Overlay plugin": "Il plugin overlay",
        "route parameters": "i parametri del percorso",
        "shutdown timeout cannot be negative": "il timeout di arresto non può essere negativo",
        "returned": "ha restituito",
        "unsupported": "non supportato",
        "unexpected": "imprevista",
        "invalid": "non valido",
        "malformed": "non valido",
        "oversized": "troppo grande",
        "no alternative": "nessun dispositivo alternativo",
        "no output-device settings": "nessuna impostazione del dispositivo di uscita",
        "no OAuth": "nessun dato OAuth di",
        "no RPC": "nessun codice RPC di",
        "did not confirm": "non ha confermato",
        "not completed": "non è stata completata",
        "not configured": "non è configurata",
        "connection was closed": "è stata chiusa",
        "closed the RPC connection": "ha chiuso la connessione RPC",
        "too large to save": "è troppo grande per essere salvata",
        "outside": "fuori da",
        "before": "prima di",
        "only": "solo",
    },
}

_additional_error_phrases = {
    "de": {
        "has no supported geometry": "enthält keine unterstützte Geometrie", "geometry exceeds its viewBox": "Geometrie überschreitet die viewBox", "has incomplete circle geometry": "hat eine unvollständige Kreisgeometrie", "must use a zero-origin viewBox": "muss eine viewBox mit Ursprung null verwenden", "viewBox must have a positive size": "viewBox muss eine positive Größe haben", "response was too large": "Antwort war zu groß", "volume range has not been confirmed": "Lautstärkebereich wurde nicht bestätigt", "must be a decimal snowflake": "muss eine dezimale Snowflake sein", "is not connected": "ist nicht verbunden", "setup is incomplete": "Einrichtung ist unvollständig", "must be a concrete topic path": "muss ein konkreter Themenpfad sein", "supports at most": "unterstützt höchstens", "Refresh monitors and select": "Aktualisieren Sie die Monitore und wählen Sie", "Refresh power plans and select": "Aktualisieren Sie die Energiesparpläne und wählen Sie", "does not expose shortcut actions": "stellt keine Tastenkürzelaktionen bereit", "URL is invalid": "URL ist ungültig", "URL must be": "URL muss sein:", "URL port is invalid": "URL-Port ist ungültig", "body must be text": "Textkörper muss Text sein", "header name is invalid": "Headername ist ungültig", "header names and values are invalid": "Headernamen und -werte sind ungültig", "headers must be": "Header müssen sein:", "method is invalid": "Methode ist ungültig", "request settings are invalid": "Anfrageeinstellungen sind ungültig", "timeout must be": "Zeitüberschreitung muss sein:", "payload must be": "Nutzdaten müssen sein:", "payload must be valid JSON": "Nutzdaten müssen gültiges JSON sein", "output requires": "Ausgabe erfordert", "Remote is not installed": "Remote ist nicht installiert", "did not confirm the requested": "hat den angeforderten Wert nicht bestätigt", "returned an invalid": "hat einen ungültigen Wert zurückgegeben:", "returned gain outside": "hat eine Verstärkung außerhalb zurückgegeben:", "index must be": "Index muss sein:", "target must be exactly": "Ziel muss genau sein:", "power plan must be": "Energiesparplan muss sein:", "volume commands require": "Lautstärkebefehle erfordern", "default-device slots": "Standardgeräte-Slots", "Volume keys": "Lautstärketasten", "speaker SOAP": "Lautsprecher-SOAP", "speaker host": "Lautsprecherhost", "strip or bus": "Strip oder Bus",
    },
    "es": {
        "has no supported geometry": "no tiene geometría compatible", "geometry exceeds its viewBox": "tiene geometría fuera de su viewBox", "has incomplete circle geometry": "tiene geometría circular incompleta", "must use a zero-origin viewBox": "debe usar una viewBox con origen cero", "viewBox must have a positive size": "la viewBox debe tener tamaño positivo", "response was too large": "respuesta era demasiado grande", "volume range has not been confirmed": "rango de volumen no se ha confirmado", "must be a decimal snowflake": "debe ser un snowflake decimal", "is not connected": "no está conectado", "setup is incomplete": "configuración está incompleta", "must be a concrete topic path": "debe ser una ruta de tema concreta", "supports at most": "admite como máximo", "Refresh monitors and select": "Actualice los monitores y seleccione", "Refresh power plans and select": "Actualice los planes de energía y seleccione", "does not expose shortcut actions": "no ofrece acciones de atajo", "URL is invalid": "URL no es válida", "URL must be": "URL debe ser", "URL port is invalid": "puerto de URL no es válido", "body must be text": "cuerpo debe ser texto", "header name is invalid": "nombre de cabecera no es válido", "header names and values are invalid": "nombres y valores de cabecera no son válidos", "headers must be": "cabeceras deben ser", "method is invalid": "método no es válido", "request settings are invalid": "ajustes de solicitud no son válidos", "timeout must be": "tiempo de espera debe estar", "payload must be": "contenido debe ser", "payload must be valid JSON": "contenido debe ser JSON válido", "output requires": "salida requiere", "Remote is not installed": "Remote no está instalado", "did not confirm the requested": "no confirmó el valor solicitado", "returned an invalid": "devolvió un valor no válido:", "returned gain outside": "devolvió una ganancia fuera de", "index must be": "índice debe estar", "target must be exactly": "destino debe ser exactamente", "power plan must be": "plan de energía debe ser", "volume commands require": "comandos de volumen requieren", "default-device slots": "slots de dispositivo predeterminado", "Volume keys": "teclas de volumen", "speaker SOAP": "SOAP del altavoz", "speaker host": "host del altavoz", "strip or bus": "tira o bus",
    },
    "fr": {
        "has no supported geometry": "ne contient aucune géométrie prise en charge", "geometry exceeds its viewBox": "a une géométrie hors de sa viewBox", "has incomplete circle geometry": "a une géométrie de cercle incomplète", "must use a zero-origin viewBox": "doit utiliser une viewBox d’origine zéro", "viewBox must have a positive size": "la viewBox doit avoir une taille positive", "response was too large": "réponse était trop volumineuse", "volume range has not been confirmed": "plage de volume n’a pas été confirmée", "must be a decimal snowflake": "doit être un snowflake décimal", "is not connected": "n’est pas connecté", "setup is incomplete": "configuration est incomplète", "must be a concrete topic path": "doit être un chemin de sujet concret", "supports at most": "prend en charge au maximum", "Refresh monitors and select": "Actualisez les moniteurs et sélectionnez", "Refresh power plans and select": "Actualisez les modes d’alimentation et sélectionnez", "does not expose shortcut actions": "n’expose aucune action de raccourci", "URL is invalid": "URL est invalide", "URL must be": "URL doit être", "URL port is invalid": "port d’URL est invalide", "body must be text": "corps doit être du texte", "header name is invalid": "nom d’en-tête est invalide", "header names and values are invalid": "noms et valeurs d’en-tête sont invalides", "headers must be": "en-têtes doivent être", "method is invalid": "méthode est invalide", "request settings are invalid": "paramètres de requête sont invalides", "timeout must be": "délai doit être compris", "payload must be": "contenu doit être", "payload must be valid JSON": "contenu doit être un JSON valide", "output requires": "sortie nécessite", "Remote is not installed": "Remote n’est pas installé", "did not confirm the requested": "n’a pas confirmé la valeur demandée", "returned an invalid": "a renvoyé une valeur invalide :", "returned gain outside": "a renvoyé un gain hors de", "index must be": "index doit être compris", "target must be exactly": "cible doit être exactement", "power plan must be": "mode d’alimentation doit être", "volume commands require": "commandes de volume nécessitent", "default-device slots": "emplacements de périphérique par défaut", "Volume keys": "touches de volume", "speaker SOAP": "SOAP de l’enceinte", "speaker host": "hôte de l’enceinte", "strip or bus": "tranche ou bus",
    },
    "it": {
        "has no supported geometry": "non contiene geometria supportata", "geometry exceeds its viewBox": "ha una geometria oltre la viewBox", "has incomplete circle geometry": "ha una geometria circolare incompleta", "must use a zero-origin viewBox": "deve usare una viewBox con origine zero", "viewBox must have a positive size": "la viewBox deve avere dimensioni positive", "response was too large": "risposta era troppo grande", "volume range has not been confirmed": "intervallo del volume non è stato confermato", "must be a decimal snowflake": "deve essere uno snowflake decimale", "is not connected": "non è connesso", "setup is incomplete": "configurazione è incompleta", "must be a concrete topic path": "deve essere un percorso argomento concreto", "supports at most": "supporta al massimo", "Refresh monitors and select": "Aggiorna i monitor e seleziona", "Refresh power plans and select": "Aggiorna i piani di alimentazione e seleziona", "does not expose shortcut actions": "non espone azioni di scelta rapida", "URL is invalid": "URL non è valido", "URL must be": "URL deve essere", "URL port is invalid": "porta URL non è valida", "body must be text": "corpo deve essere testo", "header name is invalid": "nome dell’intestazione non è valido", "header names and values are invalid": "nomi e valori delle intestazioni non sono validi", "headers must be": "intestazioni devono essere", "method is invalid": "metodo non è valido", "request settings are invalid": "impostazioni della richiesta non sono valide", "timeout must be": "timeout deve essere compreso", "payload must be": "payload deve essere", "payload must be valid JSON": "payload deve essere JSON valido", "output requires": "uscita richiede", "Remote is not installed": "Remote non è installato", "did not confirm the requested": "non ha confermato il valore richiesto", "returned an invalid": "ha restituito un valore non valido:", "returned gain outside": "ha restituito un guadagno fuori da", "index must be": "indice deve essere compreso", "target must be exactly": "destinazione deve essere esattamente", "power plan must be": "piano di alimentazione deve essere", "volume commands require": "comandi del volume richiedono", "default-device slots": "slot del dispositivo predefinito", "Volume keys": "tasti volume", "speaker SOAP": "SOAP dell’altoparlante", "speaker host": "host dell’altoparlante", "strip or bus": "strip o bus",
    },
}

_additional_error_phrases["de"].update({
    "geometry is incomplete": "Geometrie ist unvollständig", "mode must be all or current": "Modus muss all oder current sein", "do not accept parameters": "akzeptieren keine Parameter", "connection timed out": "Verbindungszeitüberschreitung", "Could not communicate with": "Kommunikation fehlgeschlagen mit", "Could not write to": "Schreiben fehlgeschlagen auf", "input does not expose": "Eingabe stellt nicht bereit:", "input plugin has not been initialized": "Eingabe-Plugin wurde nicht initialisiert", "input requires at least one mapped message": "Eingabe erfordert mindestens eine zugeordnete Nachricht", "shutdown is still in progress": "Beenden läuft noch", "mapped messages must be distinct": "zugeordnete Nachrichten müssen verschieden sein", "message type must be": "Nachrichtentyp muss sein:", "is already connected": "ist bereits verbunden", "message is too large": "Nachricht ist zu groß", "closed the": "hat geschlossen:", "did not confirm": "hat nicht bestätigt", "did not send": "hat nicht gesendet:", "does not support": "unterstützt nicht:", "settings are incomplete or contain unknown fields": "Einstellungen sind unvollständig oder enthalten unbekannte Felder", "password is invalid": "Passwort ist ungültig", "port must be": "Port muss sein:", "rejected": "hat abgelehnt:", "request type must be": "Anfragetyp muss sein:", "sent too many": "hat zu viele gesendet:", "bind address must be": "Bind-Adresse muss sein:", "addresses must be distinct": "Adressen müssen verschieden sein", "must have no argument": "darf kein Argument haben", "volume must be": "Lautstärke muss sein:", "is unavailable": "ist nicht verfügbar", "invalid OSC": "ungültige OSC-", "non-ASCII OSC": "Nicht-ASCII-OSC-", "unexpected OSC": "unerwartete OSC-", "unsupported OSC": "nicht unterstützte OSC-", "unterminated OSC": "nicht abgeschlossene OSC-", "is not supported because": "wird nicht unterstützt, weil",
})
_additional_error_phrases["es"].update({
    "geometry is incomplete": "geometría está incompleta", "mode must be all or current": "modo debe ser all o current", "do not accept parameters": "no aceptan parámetros", "connection timed out": "conexión agotó el tiempo", "Could not communicate with": "No se pudo comunicar con", "Could not write to": "No se pudo escribir en", "input does not expose": "entrada no ofrece", "input plugin has not been initialized": "plugin de entrada no se ha inicializado", "input requires at least one mapped message": "entrada requiere al menos un mensaje asignado", "shutdown is still in progress": "cierre sigue en curso", "mapped messages must be distinct": "mensajes asignados deben ser distintos", "message type must be": "tipo de mensaje debe ser", "is already connected": "ya está conectado", "message is too large": "mensaje es demasiado grande", "closed the": "cerró", "did not confirm": "no confirmó", "did not send": "no envió", "does not support": "no admite", "settings are incomplete or contain unknown fields": "ajustes están incompletos o contienen campos desconocidos", "password is invalid": "contraseña no es válida", "port must be": "puerto debe ser", "rejected": "rechazó", "request type must be": "tipo de solicitud debe ser", "sent too many": "envió demasiados", "bind address must be": "dirección de enlace debe ser", "addresses must be distinct": "direcciones deben ser distintas", "must have no argument": "no debe tener argumento", "volume must be": "volumen debe ser", "is unavailable": "no está disponible", "invalid OSC": "OSC no válido: ", "non-ASCII OSC": "OSC no ASCII: ", "unexpected OSC": "argumentos OSC inesperados: ", "unsupported OSC": "OSC no compatible: ", "unterminated OSC": "OSC sin terminar: ", "is not supported because": "no se admite porque",
})
_additional_error_phrases["fr"].update({
    "geometry is incomplete": "géométrie est incomplète", "mode must be all or current": "mode doit être all ou current", "do not accept parameters": "n’acceptent aucun paramètre", "connection timed out": "connexion a expiré", "Could not communicate with": "Impossible de communiquer avec", "Could not write to": "Impossible d’écrire sur", "input does not expose": "entrée n’expose pas", "input plugin has not been initialized": "plugin d’entrée n’a pas été initialisé", "input requires at least one mapped message": "entrée nécessite au moins un message associé", "shutdown is still in progress": "arrêt est encore en cours", "mapped messages must be distinct": "messages associés doivent être distincts", "message type must be": "type de message doit être", "is already connected": "est déjà connecté", "message is too large": "message est trop volumineux", "closed the": "a fermé", "did not confirm": "n’a pas confirmé", "did not send": "n’a pas envoyé", "does not support": "ne prend pas en charge", "settings are incomplete or contain unknown fields": "paramètres sont incomplets ou contiennent des champs inconnus", "password is invalid": "mot de passe est invalide", "port must be": "port doit être", "rejected": "a rejeté", "request type must be": "type de requête doit être", "sent too many": "a envoyé trop de", "bind address must be": "adresse d’écoute doit être", "addresses must be distinct": "adresses doivent être distinctes", "must have no argument": "ne doit avoir aucun argument", "volume must be": "volume doit être", "is unavailable": "est indisponible", "invalid OSC": "OSC invalide : ", "non-ASCII OSC": "OSC non ASCII : ", "unexpected OSC": "arguments OSC inattendus : ", "unsupported OSC": "OSC non pris en charge : ", "unterminated OSC": "OSC non terminé : ", "is not supported because": "n’est pas pris en charge car",
})
_additional_error_phrases["it"].update({
    "geometry is incomplete": "geometria è incompleta", "mode must be all or current": "modalità deve essere all o current", "do not accept parameters": "non accettano parametri", "connection timed out": "connessione è scaduta", "Could not communicate with": "Impossibile comunicare con", "Could not write to": "Impossibile scrivere su", "input does not expose": "ingresso non espone", "input plugin has not been initialized": "plugin di ingresso non è stato inizializzato", "input requires at least one mapped message": "ingresso richiede almeno un messaggio associato", "shutdown is still in progress": "arresto è ancora in corso", "mapped messages must be distinct": "messaggi associati devono essere distinti", "message type must be": "tipo di messaggio deve essere", "is already connected": "è già connesso", "message is too large": "messaggio è troppo grande", "closed the": "ha chiuso", "did not confirm": "non ha confermato", "did not send": "non ha inviato", "does not support": "non supporta", "settings are incomplete or contain unknown fields": "impostazioni sono incomplete o contengono campi sconosciuti", "password is invalid": "password non è valida", "port must be": "porta deve essere", "rejected": "ha rifiutato", "request type must be": "tipo di richiesta deve essere", "sent too many": "ha inviato troppi", "bind address must be": "indirizzo di associazione deve essere", "addresses must be distinct": "indirizzi devono essere distinti", "must have no argument": "non deve avere argomenti", "volume must be": "volume deve essere", "is unavailable": "non è disponibile", "invalid OSC": "OSC non valido: ", "non-ASCII OSC": "OSC non ASCII: ", "unexpected OSC": "argomenti OSC imprevisti: ", "unsupported OSC": "OSC non supportato: ", "unterminated OSC": "OSC non terminato: ", "is not supported because": "non è supportato perché",
})
_additional_error_phrases["de"].update({"must be a hostname": "muss ein Hostname sein", "must be an OSC address": "muss eine OSC-Adresse sein", "must be a nonzero integer": "muss eine Ganzzahl ungleich null sein"})
_additional_error_phrases["es"].update({"must be a hostname": "debe ser un nombre de host", "must be an OSC address": "debe ser una dirección OSC", "must be a nonzero integer": "debe ser un entero distinto de cero"})
_additional_error_phrases["fr"].update({"must be a hostname": "doit être un nom d’hôte", "must be an OSC address": "doit être une adresse OSC", "must be a nonzero integer": "doit être un entier non nul"})
_additional_error_phrases["it"].update({"must be a hostname": "deve essere un nome host", "must be an OSC address": "deve essere un indirizzo OSC", "must be a nonzero integer": "deve essere un intero diverso da zero"})

_plugin_error_start = re.compile(
    r"^(?:A macOS|An eISCP|Another DDC|At least|Audio |Choose |Client secret|"
    r"Configure |Could not |DDC |Default-device|Denon/Marantz|Discord |Enter |"
    r"Failed |HTTP |Keyboard |MIDI |MQTT |Mouse activity|OBS |OSC |Onkyo |Overlay |"
    r"Pioneer/Elite|Refresh |Refusing |Select |Sonos |Sony |System |The |Timed out|"
    r"Unknown |Voicemeeter |Wait |Windows |YNCA |Yamaha |invalid OSC|non-ASCII OSC|"
    r"unexpected OSC|unsupported OSC|unterminated OSC|wss://)"
)


def set_language(language: str) -> None:
    global _language
    _language = language if language in {"en", "de", "es", "fr", "it"} else "en"


def tr(key: str) -> str:
    return _translations.get(_language, {}).get(key, _english.get(key, key))


def translate_source(text: str) -> str:
    if not isinstance(text, str) or _language == "en":
        return text
    exact = _source_translations.get(_language, {}).get(text)
    if exact is not None:
        return exact
    exact = _http_error_translations.get(_language, {}).get(text)
    if exact is not None:
        return exact
    for pattern, translations in _opaque_error_templates:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        values = match.groups()
        detail = values[0] if values and values[0] is not None else ""
        return translations[_language].format(
            detail=detail,
            suffix=f": {detail}" if detail else "",
        )
    for pattern, translations in _opaque_plugin_wrappers:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        groups = match.groups()
        if groups[0] in _result_wrapper_prefixes[_language]:
            return translations[_language].format(
                prefix=_result_wrapper_prefixes[_language][groups[0]],
                detail=groups[1],
                period=groups[2] or "",
            )
        if groups[0] == "Voicemeeter rejected parameter":
            return translations[_language].format(
                value=groups[1], detail=groups[3], period=groups[4] or ""
            )
        if groups[0] == "The speaker response omitted":
            return translations[_language].format(detail=groups[1], period=groups[2] or "")
        if len(groups) == 3 and pattern.pattern.startswith("^OBS rejected"):
            return translations[_language].format(
                value=groups[0], detail=f": {groups[1]}" if groups[1] else "", code=groups[2] or ""
            )
        if len(groups) == 2:
            return translations[_language].format(value=groups[0], detail=groups[1])
        return translations[_language].format(detail=groups[0])
    keyboard = re.fullmatch(r"Keyboard (.+?) key is invalid: (.*)", text)
    if keyboard is not None:
        templates = {
            "de": "Tastaturtaste {label} ist ungültig: {detail}",
            "es": "La tecla {label} del teclado no es válida: {detail}",
            "fr": "La touche clavier {label} est invalide : {detail}",
            "it": "Il tasto {label} della tastiera non è valido: {detail}",
        }
        return templates[_language].format(label=keyboard[1], detail=keyboard[2])
    count_status = re.fullmatch(
        r"(.+?) (monitor\(s\)|paired Bluetooth (?:audio )?device\(s\)|render soundcard endpoint\(s\)|"
        r"capture endpoint\(s\)|configurable DDC monitor\(s\)|unambiguous active application audio session\(s\)) (found|discovered)(\.)?",
        text,
    )
    if count_status is not None:
        templates = {
            "de": "{count} {kind} gefunden{period}",
            "es": "Se encontraron {count} {kind}{period}",
            "fr": "{count} {kind} trouvé(s){period}",
            "it": "Trovati {count} {kind}{period}",
        }
        kinds = {
            "de": {"monitor(s)": "Monitor(e)", "paired Bluetooth device(s)": "gekoppelte Bluetooth-Gerät(e)", "paired Bluetooth audio device(s)": "gekoppelte Bluetooth-Audiogerät(e)", "render soundcard endpoint(s)": "Wiedergabeendpunkt(e)", "capture endpoint(s)": "Aufnahmeendpunkt(e)", "configurable DDC monitor(s)": "konfigurierbare DDC-Monitor(e)", "unambiguous active application audio session(s)": "eindeutige aktive Anwendungsaudiositzung(en)"},
            "es": {"monitor(s)": "monitor(es)", "paired Bluetooth device(s)": "dispositivo(s) Bluetooth emparejado(s)", "paired Bluetooth audio device(s)": "dispositivo(s) de audio Bluetooth emparejado(s)", "render soundcard endpoint(s)": "extremo(s) de reproducción", "capture endpoint(s)": "extremo(s) de captura", "configurable DDC monitor(s)": "monitor(es) DDC configurable(s)", "unambiguous active application audio session(s)": "sesión(es) de audio de aplicación activa inequívoca(s)"},
            "fr": {"monitor(s)": "moniteur(s)", "paired Bluetooth device(s)": "périphérique(s) Bluetooth associé(s)", "paired Bluetooth audio device(s)": "périphérique(s) audio Bluetooth associé(s)", "render soundcard endpoint(s)": "point(s) de lecture", "capture endpoint(s)": "point(s) de capture", "configurable DDC monitor(s)": "moniteur(s) DDC configurable(s)", "unambiguous active application audio session(s)": "session(s) audio d’application active non ambiguë(s)"},
            "it": {"monitor(s)": "monitor", "paired Bluetooth device(s)": "dispositivo/i Bluetooth associato/i", "paired Bluetooth audio device(s)": "dispositivo/i audio Bluetooth associato/i", "render soundcard endpoint(s)": "endpoint di riproduzione", "capture endpoint(s)": "endpoint di acquisizione", "configurable DDC monitor(s)": "monitor DDC configurabile/i", "unambiguous active application audio session(s)": "sessione/i audio di applicazione attiva non ambigua/e"},
        }
        return templates[_language].format(count=count_status[1], kind=kinds[_language][count_status[2]], period=count_status[4] or "")
    validation = re.fullmatch(r"(.+?) (contains a duplicate key\.|contains an invalid TCP port\.|contains an invalid character\.|is malformed\.|must be valid finite JSON\.|must not contain URL credentials\.|must not contain a fragment\.|must use http:// or https:// and include a host\.)", text)
    if validation is not None:
        return f"{validation[1]} {_dynamic_validation_suffixes[_language][validation[2]]}"
    bounded_http = re.fullmatch(r"(.+?) must be a non-empty HTTP or HTTPS URL of at most (.+?) characters\.", text)
    if bounded_http is not None:
        templates = {"de": "{label} muss eine nicht leere HTTP- oder HTTPS-URL mit höchstens {limit} Zeichen sein.", "es": "{label} debe ser una URL HTTP o HTTPS no vacía de hasta {limit} caracteres.", "fr": "{label} doit être une URL HTTP ou HTTPS non vide de {limit} caractères maximum.", "it": "{label} deve essere un URL HTTP o HTTPS non vuoto di massimo {limit} caratteri."}
        return templates[_language].format(label=bounded_http[1], limit=bounded_http[2])
    occupied = re.fullmatch(r"HTTP (headers|request body) may occupy at most (.+?) UTF-8 bytes\.", text)
    if occupied is not None:
        templates = {"de": "HTTP-{kind} dürfen höchstens {limit} UTF-8-Byte belegen.", "es": "{kind} HTTP pueden ocupar como máximo {limit} bytes UTF-8.", "fr": "{kind} HTTP peuvent occuper au maximum {limit} octets UTF-8.", "it": "{kind} HTTP possono occupare al massimo {limit} byte UTF-8."}
        kinds = {"de": {"headers": "Header", "request body": "Anfragetexte"}, "es": {"headers": "Las cabeceras", "request body": "Los cuerpos de solicitud"}, "fr": {"headers": "Les en-têtes", "request body": "Les corps de requête"}, "it": {"headers": "Le intestazioni", "request body": "I corpi delle richieste"}}
        return templates[_language].format(kind=kinds[_language][occupied[1]], limit=occupied[2])
    keep_alive_status = re.fullmatch(r"(Active|Active after mouse movement): (.+)", text)
    if keep_alive_status is not None:
        prefix = _source_translations[_language].get(keep_alive_status[1], keep_alive_status[1])
        targets = ", ".join(
            _source_translations[_language].get(target, target)
            for target in keep_alive_status[2].split(", ")
        )
        return f"{prefix}: {targets}"
    status_wrapper = re.fullmatch(
        r"(Authorization failed|Configuration failed|Discovery failed|Initialization failed|"
        r"Input change failed|Overlay creation failed|Switch failed|Unavailable|"
        r"(?:.+) keep-alive failed): (.*)",
        text,
    )
    if status_wrapper is not None:
        prefixes = {
            "de": {"Authorization failed": "Autorisierung fehlgeschlagen", "Configuration failed": "Konfiguration fehlgeschlagen", "Discovery failed": "Suche fehlgeschlagen", "Initialization failed": "Initialisierung fehlgeschlagen", "Input change failed": "Eingangswechsel fehlgeschlagen", "Overlay creation failed": "Erstellen der Einblendung fehlgeschlagen", "Switch failed": "Wechsel fehlgeschlagen", "Unavailable": "Nicht verfügbar", "Playback keep-alive failed": "Aktivhaltung der Wiedergabe fehlgeschlagen", "Voice output keep-alive failed": "Aktivhaltung der Sprachausgabe fehlgeschlagen"},
            "es": {"Authorization failed": "Falló la autorización", "Configuration failed": "Falló la configuración", "Discovery failed": "Falló la búsqueda", "Initialization failed": "Falló la inicialización", "Input change failed": "Falló el cambio de entrada", "Overlay creation failed": "Falló la creación de la superposición", "Switch failed": "Falló el cambio", "Unavailable": "No disponible", "Playback keep-alive failed": "Falló el mantenimiento de la reproducción", "Voice output keep-alive failed": "Falló el mantenimiento de la salida de voz"},
            "fr": {"Authorization failed": "Échec de l’autorisation", "Configuration failed": "Échec de la configuration", "Discovery failed": "Échec de la recherche", "Initialization failed": "Échec de l’initialisation", "Input change failed": "Échec du changement d’entrée", "Overlay creation failed": "Échec de la création de la superposition", "Switch failed": "Échec du changement", "Unavailable": "Indisponible", "Playback keep-alive failed": "Échec du maintien de la lecture", "Voice output keep-alive failed": "Échec du maintien de la sortie vocale"},
            "it": {"Authorization failed": "Autorizzazione non riuscita", "Configuration failed": "Configurazione non riuscita", "Discovery failed": "Ricerca non riuscita", "Initialization failed": "Inizializzazione non riuscita", "Input change failed": "Cambio ingresso non riuscito", "Overlay creation failed": "Creazione overlay non riuscita", "Switch failed": "Cambio non riuscito", "Unavailable": "Non disponibile", "Playback keep-alive failed": "Mantenimento della riproduzione non riuscito", "Voice output keep-alive failed": "Mantenimento dell’uscita voce non riuscito"},
        }
        prefix = prefixes[_language].get(status_wrapper[1])
        if prefix is None:
            label = status_wrapper[1].removesuffix(" keep-alive failed")
            generic = {"de": "Aktivhaltung von {label} fehlgeschlagen", "es": "Falló el mantenimiento de {label}", "fr": "Échec du maintien de {label}", "it": "Mantenimento di {label} non riuscito"}
            prefix = generic[_language].format(label=label)
        return f"{prefix}: {status_wrapper[2]}"
    if "; " in text:
        parts = text.split("; ")
        translated = [translate_source(part) for part in parts]
        if translated != parts:
            return "; ".join(translated)
    if ": " in text:
        prefix, tail = text.split(": ", 1)
        translated_prefix = _source_translations.get(_language, {}).get(prefix)
        if translated_prefix is not None:
            return f"{translated_prefix}: {tail}"
        translated_tail = _source_translations.get(_language, {}).get(tail)
        if translated_tail is not None:
            return f"{prefix}: {translated_tail}"
    if _plugin_error_start.match(text):
        translated = text
        phrases = {**_error_phrases[_language], **_additional_error_phrases[_language]}
        for source in sorted(phrases, key=len, reverse=True):
            translated = translated.replace(source, phrases[source])
        return translated
    return text
