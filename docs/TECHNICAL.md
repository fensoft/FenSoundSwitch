# Technical Documentation

This page is the technical index for maintainers and plugin authors.

## Core Design

- [Architecture](ARCHITECTURE.md): process model, thread boundaries, route instances, plugin lifecycle, overlays, and platform integrations.
- [Configuration and Security](CONFIGURATION.md): settings locations, plugin persistence, external plugin trust boundary, audio routing, logging, and security constraints.
- [Development](DEVELOPMENT.md): hardware-free validation and manual validation limits.
- [Build instructions](../BUILD.md): dependencies, executable build, and release workflow.
- [Repository rules](../AGENTS.md): Windows safety constraints and required checks.

## Plugin Model

Bundled modules are installed in the `plugins` package. The plugin API supports independent route input/output instances, ordered automation actions, per-step declarative editors, and selectable overlay renderers. Each route and configured automation step owns its own parameters. DDC monitor-input steps persist independent stable monitor/input targets and revalidate both before every action run.

Bundled route outputs include DDC, Windows endpoint/capture/Bluetooth/application sessions, generic HTTP JSON, and receiver protocols for Onkyo/Integra, Denon/Marantz, Yamaha, Pioneer/Elite, and Sony. MQTT, keyboard, and Windows media-key inputs dispatch through host-owned route boundaries. System automation exposes exact-monitor DDC brightness/contrast, bounded HTTP requests, and confirmed power-plan changes. The host owns the native Windows volume hook, serializes output operations, and publishes immutable route-status snapshots to plugins.

External plugin code is trusted in-process Python. Adjacent plugins belong in `external-plugins`; per-user plugins belong in `%APPDATA%\fensoundswitch\plugins`. `%APPDATA%\windows-ddc\plugins` is retained as a final read-only compatibility location during migration. See the architecture document before adding or changing a plugin capability.
