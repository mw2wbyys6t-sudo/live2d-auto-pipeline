"""Tests for character consistency system."""

import json
import tempfile
from pathlib import Path

import pytest
from PIL import Image
import numpy as np

from core.character.card import CharacterCard
from core.character.manager import CharacterManager
from core.character.embedding import EmbeddingExtractor


class TestCharacterCard:
    """Test CharacterCard data model."""

    def test_create_card(self):
        card = CharacterCard(name="Test Girl", personality="cheerful")
        assert card.name == "Test Girl"
        assert card.personality == "cheerful"
        assert card.character_id  # auto-generated UUID
        assert card.created_at

    def test_card_to_dict(self):
        card = CharacterCard(name="Test", hair_color="#FF00FF")
        d = card.to_dict()
        assert d["name"] == "Test"
        assert d["hair"]["color"] == "#FF00FF"
        assert "character_id" in d
        assert "face" in d
        assert "hair" in d

    def test_card_from_dict(self):
        data = {
            "character_id": "test-123",
            "name": "Hatsune Miku",
            "hair": {"color": "#00FF00", "style": "twintail"},
            "face": {"shape": "oval", "eye_color": "#00FF00"},
            "persona": {"personality": "energetic"},
        }
        card = CharacterCard.from_dict(data)
        assert card.character_id == "test-123"
        assert card.name == "Hatsune Miku"
        assert card.hair_color == "#00FF00"

    def test_card_save_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card = CharacterCard(name="Test Save", eye_color="blue")
            # save() expects a file path, not a directory
            path = card.save(str(Path(tmpdir) / "test_card.json"))
            assert Path(path).exists()

            loaded = CharacterCard.load(path)
            assert loaded.name == "Test Save"
            assert loaded.eye_color == "blue"

    def test_style_prompt_generation(self):
        card = CharacterCard(
            name="Test",
            hair_color="#0066FF",
            eye_color="#00CCFF",
            hair_style="twintail",
            face_shape="oval",
            skin_tone="#FFE0BD",
        )
        prompt = card.generate_style_prompt()
        assert "blue" in prompt.lower() or "#0066ff" in prompt.lower()
        assert "twintail" in prompt.lower() or "twin" in prompt.lower()

    def test_negative_prompt(self):
        card = CharacterCard(name="Test")
        neg = card.generate_negative_prompt()
        assert isinstance(neg, str)
        assert len(neg) > 0

    def test_face_similarity(self):
        card1 = CharacterCard(name="A", face_shape="oval", eye_size=0.6, eye_color="#FF0000")
        card2 = CharacterCard(name="B", face_shape="oval", eye_size=0.6, eye_color="#FF0000")
        card3 = CharacterCard(name="C", face_shape="round", eye_size=0.3, eye_color="#0000FF")

        sim_same = card1.compute_face_similarity(card2)
        sim_diff = card1.compute_face_similarity(card3)
        assert sim_same > sim_diff


class TestCharacterManager:
    """Test CharacterManager CRUD."""

    def test_create_character(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CharacterManager(storage_dir=tmpdir)
            card = mgr.create_character(name="Manager Test", personality="shy")
            assert card.character_id
            # Cards are stored as <id>.json files
            assert Path(tmpdir, f"{card.character_id}.json").exists()

    def test_list_characters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CharacterManager(storage_dir=tmpdir)
            mgr.create_character(name="A")
            mgr.create_character(name="B")
            chars = mgr.list_characters()
            assert len(chars) == 2

    def test_load_character(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CharacterManager(storage_dir=tmpdir)
            created = mgr.create_character(name="LoadMe", eye_color="red")
            loaded = mgr.load_character(created.character_id)
            assert loaded.name == "LoadMe"
            assert loaded.eye_color == "red"

    def test_delete_character(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CharacterManager(storage_dir=tmpdir)
            card = mgr.create_character(name="DeleteMe")
            assert mgr.delete_character(card.character_id)
            chars = mgr.list_characters()
            assert len(chars) == 0

    def test_update_character(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CharacterManager(storage_dir=tmpdir)
            card = mgr.create_character(name="Before")
            updated = mgr.update_character(card.character_id, {"name": "After"})
            assert updated.name == "After"

    def test_generation_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CharacterManager(storage_dir=tmpdir)
            card = mgr.create_character(
                name="Prompt Girl",
                hair_color="#FF69B4",
                hair_style="long",
            )
            prompt = mgr.get_generation_prompt(card.character_id, "school uniform")
            assert "school uniform" in prompt


class TestEmbeddingExtractor:
    """Test visual embedding extraction."""

    def test_histogram_fallback(self):
        """Test that histogram embedding works without torch/CLIP."""
        extractor = EmbeddingExtractor(model_name="histogram")
        img = Image.new("RGB", (128, 128), color=(100, 150, 200))
        emb = extractor.extract(img)
        assert len(emb) > 0
        assert all(isinstance(x, float) for x in emb)

    def test_color_features(self):
        extractor = EmbeddingExtractor(model_name="histogram")
        img = Image.new("RGBA", (64, 64), color=(255, 0, 0, 255))
        features = extractor._extract_color_features(img)
        assert len(features) > 0
        # Red-dominant image
        assert features[0] > 0.9  # red channel mean

    def test_similarity(self):
        extractor = EmbeddingExtractor(model_name="histogram")
        # Use distinct patterned images so histograms clearly differ
        img1 = Image.new("RGB", (64, 64), color=(255, 0, 0))
        img2 = Image.new("RGB", (64, 64), color=(255, 0, 0))  # identical
        arr = np.zeros((64, 64, 3), dtype=np.uint8)
        arr[:, :, 2] = 255  # pure blue
        img3 = Image.fromarray(arr, "RGB")

        emb1 = extractor.extract(img1)
        emb2 = extractor.extract(img2)
        emb3 = extractor.extract(img3)

        sim_identical = extractor.similarity(emb1, emb2)
        sim_different = extractor.similarity(emb1, emb3)
        # Identical images should have near-perfect similarity; different colors lower
        assert sim_identical > 0.99
        assert sim_identical > sim_different
