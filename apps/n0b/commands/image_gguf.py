"""GGUF text-encoder loading for n0b ai image.

This module runs inside the isolated AI dependency group.
"""
from __future__ import annotations

from dataclasses import fields
from typing import Any
from urllib.parse import unquote, urlparse

GGUF_TEXT_BACKBONE = {
    "mistral3": "mistral",
    "qwen3": "qwen3",
}


def parse_hub_file(url: str) -> tuple[str, str, str]:
    parts = [unquote(part) for part in urlparse(url).path.split("/") if part]
    return "/".join(parts[:2]), parts[3], "/".join(parts[4:])


def gguf_architecture(path: str) -> str:
    from gguf import GGUFReader

    field = GGUFReader(path).fields["general.architecture"]
    value = field.contents()
    if isinstance(value, (list, tuple)):
        value = value[0]
    if isinstance(value, bytes):
        value = value.decode()
    return str(value)


def enable_gguf_arch_aliases() -> None:
    """Treat llama.cpp `mistral3` GGUFs as Mistral text weights."""
    from transformers.integrations.ggml import GGUF_CONFIG_MAPPING
    from transformers import modeling_gguf_pytorch_utils as gguf_utils

    GGUF_CONFIG_MAPPING.setdefault("mistral3", GGUF_CONFIG_MAPPING["mistral"])
    if "mistral3" not in gguf_utils.GGUF_SUPPORTED_ARCHITECTURES:
        gguf_utils.GGUF_SUPPORTED_ARCHITECTURES.append("mistral3")


def _load_mistral3_text_encoder(
    model_class: Any,
    repo_id: str,
    filename: str,
    revision: str,
    config: Any,
    dtype: Any,
) -> Any:
    from transformers import modeling_gguf_pytorch_utils as gguf_utils

    original = gguf_utils.get_gguf_hf_weights_map

    def weights_map(
        hf_model: Any,
        processor: Any,
        model_type: str | None = None,
        num_layers: int | None = None,
        qual_name: str = "",
    ) -> Any:
        if model_type is None and hf_model.config.model_type == "mistral":
            model_type = "mistral3"
        return original(hf_model, processor, model_type, num_layers, qual_name)

    gguf_utils.get_gguf_hf_weights_map = weights_map
    try:
        return model_class.from_pretrained(
            repo_id,
            gguf_file=filename,
            revision=revision,
            config=config,
            dtype=dtype,
        )
    finally:
        gguf_utils.get_gguf_hf_weights_map = original


def text_config_from_gguf(parsed: dict[str, Any]) -> Any:
    """Build a text-only Transformers config from GGUF metadata."""
    from transformers import MistralConfig, Qwen3Config

    data = dict(parsed)
    model_type = GGUF_TEXT_BACKBONE.get(
        data.get("model_type", ""), data.get("model_type", "")
    )
    config_class = Qwen3Config if model_type == "qwen3" else MistralConfig
    allowed = {item.name for item in fields(config_class)}
    kwargs = {key: value for key, value in data.items() if key in allowed}
    kwargs["model_type"] = model_type
    return config_class(**kwargs)


def _qwen_head_dim(path: str) -> int:
    from gguf import GGUFReader

    field = GGUFReader(path).fields["qwen3.attention.key_length"]
    value = field.contents()
    if isinstance(value, (list, tuple)):
        value = value[0]
    return int(value)


def load_gguf_text_encoder(
    repo_id: str,
    filename: str,
    revision: str,
    tokenizer_repo: str,
    dtype: Any,
    local: str,
    arch: str,
) -> tuple[Any, Any]:
    from transformers import AutoModel, AutoTokenizer, MistralModel, Qwen3Model

    enable_gguf_arch_aliases()
    from transformers.modeling_gguf_pytorch_utils import load_gguf_checkpoint

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_repo, subfolder="tokenizer")
    if arch in GGUF_TEXT_BACKBONE:
        parsed = load_gguf_checkpoint(local, return_tensors=False)["config"]
        if arch == "qwen3":
            parsed["head_dim"] = _qwen_head_dim(local)
        config = text_config_from_gguf(parsed)
        model_class = Qwen3Model if arch == "qwen3" else MistralModel
        if arch == "mistral3":
            encoder = _load_mistral3_text_encoder(
                model_class, repo_id, filename, revision, config, dtype
            )
        else:
            encoder = model_class.from_pretrained(
                repo_id,
                gguf_file=filename,
                revision=revision,
                config=config,
                dtype=dtype,
            )
    else:
        encoder = AutoModel.from_pretrained(
            repo_id, gguf_file=filename, revision=revision, dtype=dtype
        )
    return encoder, tokenizer
