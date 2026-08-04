#!/usr/bin/env python3
"""Shared Nano Banana capability rules used by provider transports.

This is internal transport code, not user configuration. The Nano Banana 2
rules reuse the existing Gemini 3.1 Flash Image preset from
com.image.model.api.connector_PS/js/provider-registry.js. Provider wire model
IDs are contract-specific and must never be rewritten or used with another
provider contract. The Nano Banana 2 / Pro differences come from the
user-supplied EzAI API examples and capability comparison dated 2026-08-01.
"""

from __future__ import annotations

import math
import re
from typing import Any


BASE_ASPECT_RATIOS = (
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
)

MODEL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "nano-banana-2": {
        "display_name": "Nano Banana 2 / Gemini 3.1 Flash Image",
        "resolutions": ("512px", "1K", "2K", "4K"),
        "aspect_ratios": (*BASE_ASPECT_RATIOS, "1:4", "4:1", "1:8", "8:1"),
        "thinking_levels": ("minimal", "high"),
        "default_thinking_level": "minimal",
    },
    "nano-banana-pro": {
        "display_name": "Nano Banana Pro / Gemini 3 Pro Image",
        "resolutions": ("1K", "2K", "4K"),
        "aspect_ratios": BASE_ASPECT_RATIOS,
        "thinking_levels": (),
        "default_thinking_level": None,
    },
}

MODEL_CONTRACT_BINDINGS: dict[str, dict[str, str]] = {
    "ezai-banana-images": {
        "nano-banana-2": "nano-banana-2",
        "nano-banana-pro": "nano-banana-pro",
    },
    "zenmux-vertex": {
        "google/gemini-3.1-flash-image": "nano-banana-2",
    },
}

WIRE_MODEL_CONTRACTS: dict[str, str] = {}
for contract, bindings in MODEL_CONTRACT_BINDINGS.items():
    for wire_model, canonical_model in bindings.items():
        if canonical_model not in MODEL_CAPABILITIES:
            raise RuntimeError(
                f"Nano Banana contract '{contract}' maps '{wire_model}' to unknown "
                f"capability '{canonical_model}'."
            )
        normalized = wire_model.strip().lower()
        existing = WIRE_MODEL_CONTRACTS.get(normalized)
        if existing and existing != contract:
            raise RuntimeError(
                f"Nano Banana wire model ID '{wire_model}' belongs to both "
                f"'{existing}' and '{contract}'."
            )
        WIRE_MODEL_CONTRACTS[normalized] = contract


def wire_models_for(contract: str) -> tuple[str, ...]:
    bindings = MODEL_CONTRACT_BINDINGS.get(contract)
    if bindings is None:
        raise ValueError(f"Unknown Nano Banana provider contract '{contract}'.")
    return tuple(bindings)


def resolve_model(contract: str, model: str, *, required: bool = True) -> dict[str, Any] | None:
    configured = str(model or "").strip()
    bindings = MODEL_CONTRACT_BINDINGS.get(contract)
    if bindings is None:
        raise ValueError(f"Unknown Nano Banana provider contract '{contract}'.")
    canonical_by_wire_id = {wire_model.lower(): canonical for wire_model, canonical in bindings.items()}
    canonical = canonical_by_wire_id.get(configured.lower())
    if canonical is None:
        owner = WIRE_MODEL_CONTRACTS.get(configured.lower())
        if owner and owner != contract:
            raise ValueError(
                f"Nano Banana wire model ID '{configured}' belongs to provider contract "
                f"'{owner}' and cannot be used with '{contract}'."
            )
        if required:
            supported = ", ".join(bindings)
            raise ValueError(
                f"Provider contract '{contract}' does not accept Nano Banana wire model ID "
                f"'{configured}'. Use one of: {supported}."
            )
        return None
    capability = MODEL_CAPABILITIES[canonical]
    return {
        "provider_contract": contract,
        "wire_model": configured,
        "canonical_model": canonical,
        **capability,
    }


def normalize_resolution(value: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    aliases = {
        "512": "512px",
        "512px": "512px",
        "0.5k": "512px",
        ".5k": "512px",
        "1": "1K",
        "1k": "1K",
        "1024": "1K",
        "1024px": "1K",
        "2": "2K",
        "2k": "2K",
        "2048": "2K",
        "2048px": "2K",
        "4": "4K",
        "4k": "4K",
        "4096": "4K",
        "4096px": "4K",
    }
    return aliases.get(text, str(value or "").strip().upper())


def resolution_from_edge(edge: int) -> str:
    if edge <= 768:
        return "512px"
    if edge <= 1408:
        return "1K"
    if edge <= 2816:
        return "2K"
    return "4K"


def validate_resolution(contract: str, model: str, resolution: str) -> str:
    capability = resolve_model(contract, model)
    canonical = normalize_resolution(resolution)
    supported = capability["resolutions"]
    if canonical not in supported:
        raise ValueError(
            f"Model '{model}' does not support resolution '{resolution}'. "
            f"Use one of: {', '.join(supported)}."
        )
    return canonical


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def normalize_aspect(value: str) -> str:
    text = str(value or "").strip().lower()
    pair = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)", text)
    if pair:
        left = float(pair.group(1))
        right = float(pair.group(2))
        if left <= 0 or right <= 0:
            raise ValueError(f"Invalid aspect ratio '{value}'.")
        return f"{_format_number(left)}:{_format_number(right)}"

    ratio = float(text)
    if ratio <= 0:
        raise ValueError(f"Invalid aspect ratio '{value}'.")

    labels = dict.fromkeys(
        ratio_label
        for capability in MODEL_CAPABILITIES.values()
        for ratio_label in capability["aspect_ratios"]
    )
    for label in labels:
        left, right = (float(item) for item in label.split(":"))
        if math.isclose(ratio, left / right, rel_tol=1e-6, abs_tol=1e-6):
            return label
    return f"{_format_number(ratio)}:1"


def match_aspect_ratio(contract: str, model: str, value: str | float) -> str:
    capability = resolve_model(contract, model)
    text = str(value).strip().lower()
    pair = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)", text)
    if pair:
        left = float(pair.group(1))
        right = float(pair.group(2))
        if left <= 0 or right <= 0:
            raise ValueError(f"Invalid derived aspect ratio '{value}'.")
        ratio = left / right
    else:
        ratio = float(text)
        if ratio <= 0:
            raise ValueError(f"Invalid derived aspect ratio '{value}'.")

    for label in capability["aspect_ratios"]:
        left, right = (float(item) for item in label.split(":"))
        if math.isclose(ratio, left / right, rel_tol=1e-6, abs_tol=1e-6):
            return label
    raise ValueError(
        f"Model '{model}' has no supported aspect_ratio token for derived ratio '{value}'. "
        f"Use one of: {', '.join(capability['aspect_ratios'])}."
    )


def nearest_aspect_ratio(contract: str, model: str, value: str | float) -> str:
    capability = resolve_model(contract, model)
    text = str(value).strip().lower()
    pair = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)", text)
    if pair:
        left = float(pair.group(1))
        right = float(pair.group(2))
        if left <= 0 or right <= 0:
            raise ValueError(f"Invalid derived aspect ratio '{value}'.")
        ratio = left / right
    else:
        ratio = float(text)
        if ratio <= 0:
            raise ValueError(f"Invalid derived aspect ratio '{value}'.")

    def distance(label: str) -> float:
        left, right = (float(item) for item in label.split(":"))
        return abs(math.log((left / right) / ratio))

    return min(capability["aspect_ratios"], key=distance)


def aspect_from_dimensions(contract: str, model: str, width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid dimensions '{width}x{height}'.")
    return match_aspect_ratio(contract, model, width / height)


def validate_aspect(contract: str, model: str, aspect: str) -> str:
    capability = resolve_model(contract, model)
    canonical = normalize_aspect(aspect)
    supported = capability["aspect_ratios"]
    if canonical not in supported:
        raise ValueError(
            f"Model '{model}' does not support aspect ratio '{aspect}'. "
            f"Use one of: {', '.join(supported)}."
        )
    return canonical


def resolve_thinking_level(contract: str, model: str, value: str | None) -> str | None:
    capability = resolve_model(contract, model)
    configured = str(value or "").strip().lower()
    supported = capability["thinking_levels"]
    if configured:
        if configured not in supported:
            if supported:
                raise ValueError(
                    f"Model '{model}' does not support thinking_level '{value}'. "
                    f"Use one of: {', '.join(supported)}."
                )
            raise ValueError(f"Model '{model}' does not support thinking_level.")
        return configured
    return capability["default_thinking_level"]
