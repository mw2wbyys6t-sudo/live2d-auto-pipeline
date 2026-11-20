from __future__ import annotations

from typing import Any, Dict, Mapping

import numpy as np


def verify_reconstruction(layers: Mapping[str, Any], source: np.ndarray) -> Dict[str, Any]:
    source_array = np.asarray(source, dtype=np.uint8)
    if source_array.ndim != 3 or source_array.shape[2] != 4:
        raise ValueError("source must be an RGBA image")

    from core.segment_engine.composer import LayerComposer

    order = {name: index for index, name in enumerate(LayerComposer.STANDARD_LAYER_ORDER)}
    names = sorted(layers, key=lambda name: (order.get(name, len(order)), name))
    included = []
    excluded = []
    for name in names:
        value = layers[name]
        if isinstance(value, dict):
            if value.get("synthesized") or value.get("completed"):
                excluded.append(name)
                continue
            value = value.get("image", value.get("path"))
        if isinstance(value, (str, bytes)):
            from PIL import Image
            value = np.asarray(Image.open(value).convert("RGBA"))
        array = np.asarray(value, dtype=np.uint8)
        if array.shape != source_array.shape:
            raise ValueError(f"layer {name} shape {array.shape} != source {source_array.shape}")
        included.append(array)

    reconstructed = LayerComposer()._alpha_over(included, *source_array.shape[:2])
    if reconstructed is None:
        reconstructed = np.zeros_like(source_array)
    delta = np.abs(reconstructed.astype(np.int16) - source_array.astype(np.int16))
    return {
        "mae": float(delta.mean()),
        "max_error": int(delta.max(initial=0)),
        "included_layers": len(included),
        "excluded_layers": excluded,
    }
