"""Inspect Zenbot botling presets, model capabilities, and resolved settings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def _load_runtime_modules():
    """Import runtime modules after ensuring `zb_app` is importable."""

    app_root = Path(__file__).resolve().parents[1]
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))

    from config import Config
    from models import LEGACY_FINETUNES

    return Config, LEGACY_FINETUNES


def _build_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Translate CLI flags into one session-settings override payload."""

    overrides: dict[str, Any] = {}
    if args.preset:
        overrides["preset_id"] = args.preset
    if args.model:
        overrides["model_name"] = args.model
    if args.reasoning_effort:
        overrides["reasoning_effort"] = args.reasoning_effort
    if args.temperature is not None:
        overrides["temperature"] = args.temperature
    if args.top_p is not None:
        overrides["top_p"] = args.top_p
    if args.max_output_tokens is not None:
        overrides["max_output_tokens"] = args.max_output_tokens

    for key in (
        "enable_function_tools",
        "enable_file_search",
        "enable_web_search",
        "enable_background_critic",
    ):
        value = getattr(args, key, None)
        if value is not None:
            overrides[key] = value
    return overrides


def _runtime_profile_name(config: Any, profile_name: str | None) -> str:
    """Return a valid configured profile name."""

    profile = str(profile_name or config.DEFAULT_RESPONSE_PROFILE).strip()
    if profile not in config.MODEL_ARGS:
        return next(iter(config.MODEL_ARGS), "live")
    return profile


def _default_model_key(config: Any) -> str:
    """Return the configured default model key from the active live registry."""

    model_key = config.MODEL_NAME
    if model_key not in config.MODELS_IN_USE:
        return next(iter(config.MODELS_IN_USE))
    return model_key


def _tool_caps(config: Any) -> dict[str, bool]:
    """Return deployment-level tool capability flags."""

    return dict(config.tool_caps())


def _botling_preset_definition(config: Any, preset_id: str) -> dict[str, Any]:
    """Return one configured preset or raise a validation error."""

    key = str(preset_id or "").strip() or config.DEFAULT_BOTLING_ID
    preset = config.BOTLING_PRESETS.get(key)
    if preset is None:
        raise ValueError(f"Unsupported preset_id: {preset_id!r}")
    return {"id": key, **preset}


def _resolve_session_settings(
    config: Any,
    raw_settings: dict[str, Any] | None,
    *,
    fallback_model_name: str = "",
    profile_name: str | None = None,
) -> dict[str, Any]:
    """Resolve preset defaults plus overrides into one inspection snapshot."""

    profile = _runtime_profile_name(config, profile_name)
    input_data = dict(raw_settings or {})
    preset_id = str(input_data.pop("preset_id", "")).strip() or config.DEFAULT_BOTLING_ID
    preset = _botling_preset_definition(config, preset_id)
    resolved: dict[str, Any] = {
        "preset_id": preset_id,
        **dict(preset.get("settings", {})),
    }
    default_profile = dict(config.MODEL_ARGS.get(profile, {}))
    resolved.update(input_data)

    model_name = str(
        resolved.get("model_name") or fallback_model_name or _default_model_key(config)
    ).strip()
    if model_name not in config.MODELS_IN_USE:
        raise ValueError(f"Unsupported model_name: {model_name!r}")
    resolved["model_name"] = model_name

    model_caps = config.model_capabilities(model_name)
    requested_reasoning = config._normalize_reasoning_effort(
        str(
            resolved.get("reasoning_effort", "")
            or default_profile.get("reasoning_effort", "")
        )
    )
    if model_caps["supports_reasoning"]:
        if (
            requested_reasoning
            and requested_reasoning not in model_caps["reasoning_efforts"]
        ):
            raise ValueError(
                f"Unsupported reasoning_effort '{requested_reasoning}' for {model_name}."
            )
        resolved["reasoning_effort"] = requested_reasoning
    else:
        if "reasoning_effort" in input_data and requested_reasoning:
            raise ValueError(f"reasoning_effort is not supported by {model_name}.")
        resolved["reasoning_effort"] = ""

    if model_caps["supports_sampling_controls"]:
        if resolved.get("temperature") is None and "temperature" in default_profile:
            resolved["temperature"] = default_profile["temperature"]
        if resolved.get("top_p") is None and "top_p" in default_profile:
            resolved["top_p"] = default_profile["top_p"]
    else:
        if input_data.get("temperature") is not None:
            raise ValueError(f"temperature is not supported by {model_name}.")
        if input_data.get("top_p") is not None:
            raise ValueError(f"top_p is not supported by {model_name}.")
        resolved["temperature"] = None
        resolved["top_p"] = None

    if resolved.get("max_output_tokens") is None:
        resolved["max_output_tokens"] = int(default_profile.get("max_output_tokens", 900))

    tool_caps = _tool_caps(config)
    for key, cap_enabled in tool_caps.items():
        explicit_value = input_data.get(key, None)
        base_value = bool(resolved.get(key, False))
        if explicit_value is True and not cap_enabled:
            raise ValueError(f"{key} is not available in this deployment.")
        if explicit_value is None:
            resolved[key] = bool(base_value and cap_enabled)
        else:
            resolved[key] = bool(explicit_value and cap_enabled)

    return {
        "preset_id": str(resolved.get("preset_id", "")).strip(),
        "model_name": str(resolved.get("model_name", "")).strip(),
        "reasoning_effort": str(resolved.get("reasoning_effort", "")).strip(),
        "temperature": resolved.get("temperature"),
        "top_p": resolved.get("top_p"),
        "max_output_tokens": int(resolved.get("max_output_tokens", 900)),
        "enable_function_tools": bool(resolved.get("enable_function_tools", False)),
        "enable_file_search": bool(resolved.get("enable_file_search", False)),
        "enable_web_search": bool(resolved.get("enable_web_search", False)),
        "enable_background_critic": bool(
            resolved.get("enable_background_critic", False)
        ),
    }


def _session_options_payload(config: Any) -> dict[str, Any]:
    """Build the session-options payload without importing runtime utilities."""

    defaults = _resolve_session_settings(config, None)
    presets = [
        {
            "id": preset_id,
            "label": preset["label"],
            "description": preset["description"],
            "settings": _resolve_session_settings(config, {"preset_id": preset_id}),
        }
        for preset_id, preset in config.BOTLING_PRESETS.items()
    ]
    models = [
        dict(config.model_capabilities(model_name)) for model_name in config.MODELS_IN_USE
    ]
    return {
        "settings_version": config.SESSION_SETTINGS_VERSION,
        "defaults": defaults,
        "presets": presets,
        "models": models,
        "tool_caps": _tool_caps(config),
    }


def _render_text(payload: dict[str, Any]) -> str:
    """Render a compact human-readable summary."""

    lines: list[str] = []
    defaults = payload["defaults"]
    lines.append("Zenbot Botling Inspector")
    lines.append(f"default preset: {payload['default_preset_id']}")
    lines.append(f"default model: {payload['default_model_name']}")
    lines.append(f"default profile: {payload['default_profile']}")
    lines.append("")
    lines.append("deployment tool caps:")
    for key, value in payload["tool_caps"].items():
        lines.append(f"  {key}: {'on' if value else 'off'}")

    lines.append("")
    lines.append("resolved settings:")
    for key, value in defaults.items():
        lines.append(f"  {key}: {value}")

    lines.append("")
    lines.append("active models:")
    for model in payload["models"]:
        effort_text = ", ".join(model["reasoning_efforts"]) or "-"
        lines.append(
            "  "
            f"{model['id']}: reasoning={model['supports_reasoning']} "
            f"sampling={model['supports_sampling_controls']} efforts=[{effort_text}]"
        )

    lines.append("")
    lines.append("presets:")
    for preset in payload["presets"]:
        settings = preset["settings"]
        lines.append(f"  {preset['id']}: {preset['label']}")
        lines.append(f"    {preset['description']}")
        lines.append(
            "    "
            f"model={settings['model_name']} "
            f"reasoning={settings['reasoning_effort'] or '-'} "
            f"max_tokens={settings['max_output_tokens']}"
        )

    if payload["legacy_finetunes"]:
        lines.append("")
        lines.append("legacy finetunes:")
        for key, value in payload["legacy_finetunes"].items():
            lines.append(f"  {key}: {value}")

    return "\n".join(lines)


def main() -> int:
    """Parse CLI flags and print the current botling runtime configuration."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset",
        default="",
        help="Preset ID to inspect or override, for example balanced_mumon.",
    )
    parser.add_argument(
        "--model",
        default="",
        help=(
            "Model key override, for example set03-bs2lr05e7 or "
            "set03a-bs5lr05e5."
        ),
    )
    parser.add_argument(
        "--profile",
        default="live",
        help="Runtime response profile used for default values.",
    )
    parser.add_argument(
        "--reasoning-effort",
        default="",
        help="Optional reasoning effort override.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Optional sampling temperature override.",
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=None,
        help="Optional top-p override.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="Optional max-output-tokens override.",
    )
    parser.add_argument(
        "--enable-function-tools",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Explicitly enable or disable local function tools.",
    )
    parser.add_argument(
        "--enable-file-search",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Explicitly enable or disable OpenAI file search.",
    )
    parser.add_argument(
        "--enable-web-search",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Explicitly enable or disable OpenAI web search.",
    )
    parser.add_argument(
        "--enable-background-critic",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Explicitly enable or disable the background critic.",
    )
    parser.add_argument(
        "--legacy-finetunes",
        action="store_true",
        help="Include the archived fine-tuned model catalog in the output.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )
    args = parser.parse_args()

    Config, LEGACY_FINETUNES = _load_runtime_modules()
    config = Config()
    option_payload = _session_options_payload(config)
    overrides = _build_overrides(args)
    resolved = _resolve_session_settings(
        config,
        overrides or None,
        fallback_model_name=args.model.strip(),
        profile_name=args.profile,
    )

    payload = {
        "default_preset_id": config.DEFAULT_BOTLING_ID,
        "default_model_name": config.MODEL_NAME,
        "default_profile": args.profile,
        "tool_caps": option_payload["tool_caps"],
        "models": option_payload["models"],
        "presets": option_payload["presets"],
        "defaults": resolved,
        "model_capabilities": config.model_capabilities(resolved["model_name"]),
        "legacy_finetunes": LEGACY_FINETUNES if args.legacy_finetunes else {},
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(_render_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
