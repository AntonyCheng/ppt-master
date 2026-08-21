"""Encryption and OpenCode configuration generation for managed providers."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status

from .config import get_settings
from .models import Provider, ProviderModel


def _cipher() -> Fernet:
    key = (get_settings().config_encryption_key or "").strip()
    if not key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "未配置 PPTMASTER_CONFIG_ENCRYPTION_KEY")
    try:
        return Fernet(key.encode())
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "PPTMASTER_CONFIG_ENCRYPTION_KEY 无效") from exc


def encrypt_api_key(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode()


def decrypt_api_key(value: str) -> str:
    try:
        return _cipher().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Provider API Key 无法解密") from exc


def key_hint(value: str) -> str:
    return "已配置" if len(value) < 8 else f"{value[:3]}...{value[-4:]}"


def opencode_config(provider: Provider, model: ProviderModel) -> dict:
    """Return one least-privilege OpenCode config for a single queued model."""

    return {
        "$schema": "https://opencode.ai/config.json",
        "disabled_providers": ["opencode"],
        "permission": {
            "edit": "allow",
            "bash": "allow",
            "webfetch": "allow",
            "external_directory": "allow",
        },
        "provider": {
            provider.slug: {
                "npm": "@ai-sdk/openai-compatible",
                "name": provider.display_name,
                "options": {"baseURL": provider.base_url, "apiKey": decrypt_api_key(provider.api_key_ciphertext)},
                "models": {model.model_id: {"name": model.display_name}},
            }
        },
        "model": f"{provider.slug}/{model.model_id}",
    }
