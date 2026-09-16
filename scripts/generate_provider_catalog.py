#!/usr/bin/env python3
"""Génère le catalogue providers/modèles CONCLAVE depuis models.dev.

Source : https://models.dev/api.json (le catalogue utilisé par opencode).
Usage :
    python scripts/generate_provider_catalog.py [chemin_api_json] [-o sortie]

Le catalogue est un instantané COMMITÉ (déterministe, testable, sans dépendance
réseau en production). Relancez ce script pour le rafraîchir.

Sécurité : seuls les endpoints de la liste CURATED sont retenus — une base URL
n'est jamais saisie par l'utilisateur (pas de SSRF). Ajouter un provider =
l'ajouter à CURATED puis régénérer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "backend" / "app" / "providers" / "catalog_data.json"

# provider_id models.dev -> (adapter, base_url, label_fr)
# adapter : "openai_compatible" | "anthropic" | "gemini" | "minimax"
CURATED: dict[str, tuple[str, str, str]] = {
    # --- natifs CONCLAVE ---
    "openai": ("openai_compatible", "https://api.openai.com/v1", "OpenAI"),
    "anthropic": ("anthropic", "https://api.anthropic.com/v1", "Anthropic"),
    "gemini": (
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta",
        "Google Gemini",
    ),    "minimax": ("minimax", "https://api.minimax.io/v1", "MiniMax"),
    # --- OpenAI-compatibles majeurs ---
    "deepseek": ("openai_compatible", "https://api.deepseek.com", "DeepSeek"),
    "groq": ("openai_compatible", "https://api.groq.com/openai/v1", "Groq"),
    "mistral": ("openai_compatible", "https://api.mistral.ai/v1", "Mistral"),
    "xai": ("openai_compatible", "https://api.x.ai/v1", "xAI (Grok)"),
    "togetherai": ("openai_compatible", "https://api.together.xyz/v1", "Together AI"),
    "cerebras": ("openai_compatible", "https://api.cerebras.ai/v1", "Cerebras"),
    "deepinfra": (
        "openai_compatible",
        "https://api.deepinfra.com/v1/openai",
        "DeepInfra",
    ),
    "fireworks-ai": (
        "openai_compatible",
        "https://api.fireworks.ai/inference/v1",
        "Fireworks AI",
    ),
    "openrouter": (
        "openai_compatible",
        "https://openrouter.ai/api/v1",
        "OpenRouter",
    ),
    "novita-ai": ("openai_compatible", "https://api.novita.ai/openai", "NovitaAI"),
    "nebius": (
        "openai_compatible",
        "https://api.tokenfactory.nebius.com/v1",
        "Nebius",
    ),
    "siliconflow": (
        "openai_compatible",
        "https://api.siliconflow.com/v1",
        "SiliconFlow",
    ),
    "huggingface": (
        "openai_compatible",
        "https://router.huggingface.co/v1",
        "Hugging Face",
    ),
    "nvidia": (
        "openai_compatible",
        "https://integrate.api.nvidia.com/v1",
        "NVIDIA NIM",
    ),
    "baseten": ("openai_compatible", "https://inference.baseten.co/v1", "Baseten"),
    "zai": ("openai_compatible", "https://api.z.ai/api/paas/v4", "Z.AI (GLM)"),
    "zhipuai": (
        "openai_compatible",
        "https://open.bigmodel.cn/api/paas/v4",
        "Zhipu AI",
    ),
    "alibaba": (
        "openai_compatible",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "Alibaba (Qwen)",
    ),
    "moonshotai": ("openai_compatible", "https://api.moonshot.ai/v1", "Moonshot (Kimi)"),
    "xiaomi": ("openai_compatible", "https://api.xiaomimimo.com/v1", "Xiaomi MiMo"),
    "opencode": ("openai_compatible", "https://opencode.ai/zen/v1", "OpenCode Zen"),
    "opencode-go": (
        "openai_compatible",
        "https://opencode.ai/zen/go/v1",
        "OpenCode Go",
    ),
}

#: provider_id CONCLAVE -> id models.dev (quand différents)
SOURCE_IDS: dict[str, str] = {"gemini": "google"}

#: Modèles CONCLAVE exige le tool calling ; on écarte le reste.
MAX_MODELS_PER_PROVIDER = 20


def _pricing(model: dict) -> dict[str, float] | None:
    cost = model.get("cost")
    if not isinstance(cost, dict):
        return None
    inp = cost.get("input")
    out = cost.get("output")
    if inp is None or out is None:
        return None
    try:
        inp_f = float(inp)
        out_f = float(out)
    except (TypeError, ValueError):
        return None
    if inp_f <= 0 and out_f <= 0:
        return None
    return {
        "input_usd_per_million_tokens": inp_f,
        "output_usd_per_million_tokens": out_f,
    }


def _models(provider: dict) -> list[dict]:
    models = provider.get("models", {})
    tool = [m for m in models.values() if m.get("tool_call")]
    tool.sort(
        key=lambda m: (m.get("last_updated") or m.get("release_date") or "", m.get("id", "")),
        reverse=True,
    )
    result: list[dict] = []
    for model in tool[:MAX_MODELS_PER_PROVIDER]:
        result.append(
            {
                "model_id": model.get("id"),
                "label": model.get("name") or model.get("id"),
                "supports_tools": True,
                "supports_streaming": True,
                "supports_structured_output": bool(model.get("structured_output", True)),
                "supports_reasoning": bool(model.get("reasoning", False)),
                "context": (model.get("limit") or {}).get("context"),
                "max_output": (model.get("limit") or {}).get("output"),
                "pricing": _pricing(model),
            }
        )
    return [m for m in result if m["model_id"]]


def build(api_json: dict) -> dict:
    providers: list[dict] = []
    for pid, (adapter, base_url, label) in CURATED.items():
        provider = api_json.get(SOURCE_IDS.get(pid, pid))
        if provider is None:
            print(f"  ! provider absent du catalogue source : {pid}", file=sys.stderr)
            continue
        models = _models(provider)
        if not models:
            print(f"  ! aucun modèle tool-capable pour {pid}", file=sys.stderr)
            continue
        providers.append(
            {
                "provider_id": pid,
                "label": label,
                "adapter": adapter,
                "base_url": base_url,
                "env": (provider.get("env") or [""])[0],
                "auth_modes": ["api_key"],
                "supports_reasoning": any(m["supports_reasoning"] for m in models),
                "models": models,
            }
        )
    return {
        "source": "https://models.dev/api.json",
        "providers": providers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default="/tmp/models_dev.json")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    src = Path(args.source)
    if not src.exists():
        print(
            f"Source introuvable : {src}\n"
            "Téléchargez-la : curl -sL https://models.dev/api.json -o /tmp/models_dev.json",
            file=sys.stderr,
        )
        return 1

    api_json = json.loads(src.read_text())
    catalog = build(api_json)
    out = Path(args.output)
    out.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    total_models = sum(len(p["models"]) for p in catalog["providers"])
    print(f"OK — {len(catalog['providers'])} providers, {total_models} modèles -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())