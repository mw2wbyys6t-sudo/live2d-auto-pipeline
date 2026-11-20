import json
import hashlib

import numpy as np
from PIL import Image

from core.segment_engine.composer import LayerComposer


def test_compose_writes_pixel_anchored_layer_provenance(tmp_path):
    source = np.zeros((64, 64, 4), dtype=np.uint8)
    source[..., :3] = (30, 60, 90)
    source[..., 3] = 255
    mask = np.zeros((64, 64), dtype=bool)
    mask[20:40, 20:40] = True

    layers = LayerComposer().compose({"hand_L": mask}, Image.fromarray(source), str(tmp_path))

    provenance_path = tmp_path / "layer_provenance.json"
    assert provenance_path.is_file()
    manifest = json.loads(provenance_path.read_text(encoding="utf-8"))
    layer = manifest["layers"]["hand_L"]
    assert layer["bbox"] == [20, 20, 39, 39]
    assert layer["origin"] == [0, 0]
    assert layer["synthesized"] is False
    assert layer["alpha_checksum"] == hashlib.sha256(
        np.asarray(Image.open(layers["hand_L"]["path"]))[..., 3].tobytes()
    ).hexdigest()
