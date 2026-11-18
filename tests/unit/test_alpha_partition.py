import numpy as np

from core.segment_engine.composer import LayerComposer


def test_soft_alpha_partition_is_complementary_and_reconstructs_source():
    source = np.array([[[100, 120, 140, 255]]], dtype=np.uint8)
    first = np.array([[0.25]], dtype=np.float32)
    second = np.array([[0.75]], dtype=np.float32)

    layers = LayerComposer.partition_alpha(source, first, second)

    assert int(layers[0][0, 0, 3]) + int(layers[1][0, 0, 3]) == 255
    reconstructed = LayerComposer.composite_partition(layers)
    assert np.max(np.abs(reconstructed.astype(int) - source.astype(int))) <= 1
