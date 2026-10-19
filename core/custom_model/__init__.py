"""User-trained model integration (manifest + ONNX adapter)."""

from core.custom_model.manifest import (
    DEFAULT_ROOTS,
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    SUPPORTED_TASKS,
    TASK_DEPTH,
    TASK_SEGMENTATION,
    CustomModelManifest,
    discover,
    find_task,
    load_manifest,
    parse_manifest,
    resolve_roots,
    validate_manifest,
)
from core.custom_model.runner import (
    postprocess_depth,
    postprocess_segmentation,
    preprocess,
    probe,
    run,
)

__all__ = [
    "DEFAULT_ROOTS",
    "MANIFEST_FILENAME",
    "SCHEMA_VERSION",
    "SUPPORTED_TASKS",
    "TASK_DEPTH",
    "TASK_SEGMENTATION",
    "CustomModelManifest",
    "discover",
    "find_task",
    "load_manifest",
    "parse_manifest",
    "resolve_roots",
    "validate_manifest",
    "postprocess_depth",
    "postprocess_segmentation",
    "preprocess",
    "probe",
    "run",
]
