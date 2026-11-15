from __future__ import annotations

from collections.abc import Callable
from typing import Mapping
from uuid import uuid4


PROFILE_SCHEMA_VERSION = 1
AVR_INPUT_ACTION = "set-input"
AVR_POWER_ON_ACTION = "power-on"
AVR_POWER_OFF_ACTION = "power-off"


def load_profiles(raw: object, validate: Callable[[object], object]) -> dict[str, dict[str, object]]:
    """Load named AVR profiles, accepting one legacy unprofiled config."""
    profiles: dict[str, dict[str, object]] = {}
    if isinstance(raw, dict) and raw.get("schema_version") == PROFILE_SCHEMA_VERSION and isinstance(raw.get("profiles"), list):
        for item in raw["profiles"]:
            if not isinstance(item, dict):
                continue
            profile_id = item.get("id")
            name = item.get("name")
            config = item.get("config")
            if not isinstance(profile_id, str) or not profile_id.strip() or not isinstance(name, str) or not name.strip() or not isinstance(config, dict):
                continue
            checked = validate(config)
            if checked is None:
                continue
            profiles[profile_id.strip()] = {"id": profile_id.strip(), "name": name.strip(), "config": dict(config)}
        return profiles
    checked = validate(raw)
    if checked is not None and isinstance(raw, dict):
        profiles["default"] = {"id": "default", "name": "Default", "config": {"host": raw.get("host"), "port": raw.get("port")}}
    return profiles


def profile_options(profiles: Mapping[str, Mapping[str, object]], *, include_new: bool = False) -> list[dict[str, object]]:
    options = [{"label": "New profile", "value": ""}] if include_new else []
    options.extend({"label": str(profile["name"]), "value": profile_id} for profile_id, profile in profiles.items())
    return options


def new_profile_id() -> str:
    return f"avr-{uuid4().hex[:12]}"


def save_payload(profiles: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    return {"schema_version": PROFILE_SCHEMA_VERSION, "profiles": [dict(profile) for profile in profiles.values()]}


def slot_actions() -> list[object]:
    # Imported lazily so this small persistence helper remains usable without the
    # plugin API during settings migration/tests.
    from plugin_api import SlotAction
    return [
        SlotAction(AVR_INPUT_ACTION, "Set AVR input", "Select an input on a configured AVR profile."),
        SlotAction(AVR_POWER_ON_ACTION, "Turn AVR on", "Powers on a configured AVR profile."),
        SlotAction(AVR_POWER_OFF_ACTION, "Turn AVR off", "Powers off a configured AVR profile."),
    ]


def slot_ui(action_id: str, parameters: Mapping[str, object], profiles: Mapping[str, Mapping[str, object]], inputs: list[dict[str, object]]) -> dict[str, object]:
    from plugin_api import plugin_ui_document
    if action_id not in {AVR_INPUT_ACTION, AVR_POWER_ON_ACTION, AVR_POWER_OFF_ACTION}:
        raise ValueError(f"Unknown AVR action {action_id!r}.")
    fields: list[dict[str, object]] = [{
        "id": "profile_id", "type": "choice", "label": "AVR profile",
        "value": parameters.get("profile_id", ""), "options": profile_options(profiles), "required": True,
    }]
    if action_id == AVR_INPUT_ACTION:
        fields.append({
            "id": "input", "type": "choice", "label": "Input", "value": parameters.get("input", ""),
            "options": inputs, "required": True,
        })
    return plugin_ui_document("Configure AVR action", fields, [{"id": "save", "label": "Save", "kind": "submit", "async": False}], "Select an AVR profile configured under Integrations.")


def normalize_slot_parameters(action_id: str, values: Mapping[str, object], profiles: Mapping[str, Mapping[str, object]], input_values: set[str]) -> dict[str, object]:
    profile_id = values.get("profile_id")
    if not isinstance(profile_id, str) or profile_id not in profiles:
        raise ValueError("Select a configured AVR profile.")
    result: dict[str, object] = {"profile_id": profile_id}
    if action_id == AVR_INPUT_ACTION:
        input_name = values.get("input")
        if not isinstance(input_name, str) or input_name not in input_values or not input_name:
            raise ValueError("Select a valid AVR input.")
        result["input"] = input_name
    elif action_id not in {AVR_POWER_ON_ACTION, AVR_POWER_OFF_ACTION}:
        raise ValueError(f"Unknown AVR action {action_id!r}.")
    return result


def slot_summary(action_id: str, parameters: Mapping[str, object], profiles: Mapping[str, Mapping[str, object]]) -> str:
    profile = profiles.get(parameters.get("profile_id"))
    if profile is None:
        return "Not configured"
    if action_id == AVR_INPUT_ACTION:
        return f"{profile['name']} / {parameters.get('input', 'Not configured')}"
    if action_id == AVR_POWER_ON_ACTION:
        return f"{profile['name']} / On"
    if action_id == AVR_POWER_OFF_ACTION:
        return f"{profile['name']} / Off"
    return "Not configured"


def profile_config(parameters: Mapping[str, object], profiles: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    profile = profiles.get(parameters.get("profile_id"))
    if profile is None or not isinstance(profile.get("config"), dict):
        raise ValueError("Select a configured AVR profile.")
    return dict(profile["config"])
