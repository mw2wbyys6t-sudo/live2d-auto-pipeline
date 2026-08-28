#!/usr/bin/env python3
"""
Live2D Master Agent - Character Manager

CRUD operations for CharacterCard objects stored as JSON files,
plus reference-image embedding extraction and generation-prompt
assembly.
"""

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from core.character.card import CharacterCard
from core.character.embedding import EmbeddingExtractor
from core.logger import get_logger

log = get_logger("character.manager")


class CharacterManager:
    """Manage CharacterCard persistence and prompt assembly.

    Cards are stored as ``<character_id>.json`` files inside
    *storage_dir*. Reference images are copied into a ``references/``
    subdirectory alongside the card file.
    """

    def __init__(self, storage_dir: str = "assets/characters") -> None:
        """Initialize the manager.

        Args:
            storage_dir: Directory for character card JSON files.
                Created if it does not exist.
        """
        self.storage_dir: Path = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._extractor = EmbeddingExtractor(model_name="clip")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _card_path(self, character_id: str) -> Path:
        """Return the JSON path for a given character ID."""
        return self.storage_dir / f"{character_id}.json"

    def _references_dir(self, character_id: str) -> Path:
        """Return (and create) the references directory for a character."""
        ref_dir = self.storage_dir / character_id / "references"
        ref_dir.mkdir(parents=True, exist_ok=True)
        return ref_dir

    def _generate_character_id(self) -> str:
        """Generate a new UUID string for a character.

        Returns:
            A UUID4 hex string.
        """
        return uuid.uuid4().hex

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_character(
        self,
        name: str,
        reference_image: Optional[str] = None,
        **kwargs: Any,
    ) -> CharacterCard:
        """Create and persist a new character.

        Args:
            name: Character display name.
            reference_image: Optional path to a reference image. If
                provided, the image is copied into storage and its
                visual embedding is extracted.
            **kwargs: Additional fields forwarded to
                :class:`CharacterCard`.

        Returns:
            The newly created and saved CharacterCard.
        """
        character_id = self._generate_character_id()
        card = CharacterCard(character_id=character_id, name=name, **kwargs)

        if reference_image:
            try:
                self.add_reference_image(character_id, reference_image, view="front")
                # Reload the card with embedding populated
                card = self.load_character(character_id)
            except Exception as e:
                log.warning(f"Failed to process reference image: {e}")

        self.save_character(card)
        log.info(f"Created character: {name} ({character_id})")
        return card

    def load_character(self, character_id: str) -> CharacterCard:
        """Load a character card from storage.

        Args:
            character_id: The character's unique ID.

        Returns:
            The loaded CharacterCard.

        Raises:
            FileNotFoundError: If the card does not exist.
        """
        path = self._card_path(character_id)
        if not path.exists():
            raise FileNotFoundError(f"Character not found: {character_id}")
        return CharacterCard.load(str(path))

    def save_character(self, card: CharacterCard) -> str:
        """Persist a character card to storage.

        Args:
            card: The CharacterCard to save.

        Returns:
            The path the card was written to.
        """
        path = self._card_path(card.character_id)
        return card.save(str(path))

    def list_characters(self) -> List[Dict[str, Any]]:
        """List all stored characters with basic info.

        Returns:
            A list of dicts with keys: character_id, name, created_at,
            updated_at, has_embedding, hair_color, eye_color.
        """
        results: List[Dict[str, Any]] = []
        for json_file in sorted(self.storage_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                results.append({
                    "character_id": data.get("character_id"),
                    "name": data.get("name"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "has_embedding": data.get("embedding") is not None,
                    "hair_color": data.get("hair", {}).get("color"),
                    "eye_color": data.get("face", {}).get("eye_color"),
                })
            except Exception as e:
                log.warning(f"Skipping invalid character file {json_file}: {e}")
        return results

    def delete_character(self, character_id: str) -> bool:
        """Delete a character card and its reference directory.

        Args:
            character_id: The character's unique ID.

        Returns:
            True if the character was deleted, False if it didn't exist.
        """
        path = self._card_path(character_id)
        if not path.exists():
            return False
        path.unlink()

        # Also remove reference image directory
        ref_dir = self.storage_dir / character_id
        if ref_dir.exists():
            import shutil
            shutil.rmtree(ref_dir, ignore_errors=True)

        log.info(f"Deleted character: {character_id}")
        return True

    def update_character(self, character_id: str, updates: Dict[str, Any]) -> CharacterCard:
        """Update fields on an existing character.

        Args:
            character_id: The character's unique ID.
            updates: Dict of fields to update. Supports nested access
                via ``"face.eye_color"`` dotted keys or top-level keys
                like ``"name"``/``"personality"``.

        Returns:
            The updated and re-saved CharacterCard.

        Raises:
            FileNotFoundError: If the character doesn't exist.
        """
        card = self.load_character(character_id)

        for key, value in updates.items():
            if "." in key:
                # Nested update: e.g. "face.eye_color"
                parts = key.split(".", 1)
                group, field = parts
                # We update via the flat attribute on the card
                attr_map = {
                    "face.shape": "face_shape",
                    "face.eye_shape": "eye_shape",
                    "face.eye_color": "eye_color",
                    "face.eye_size": "eye_size",
                    "face.eyebrow_shape": "eyebrow_shape",
                    "face.nose_type": "nose_type",
                    "face.mouth_type": "mouth_type",
                    "face.face_width_ratio": "face_width_ratio",
                    "face.face_height_ratio": "face_height_ratio",
                    "hair.color": "hair_color",
                    "hair.style": "hair_style",
                    "hair.length": "hair_length",
                    "hair.bangs_style": "bangs_style",
                    "hair.highlights_color": "highlights_color",
                    "body.type": "body_type",
                    "body.height_ratio": "height_ratio",
                    "body.proportions": "proportions",
                    "palette.skin_tone": "skin_tone",
                    "palette.accent_color": "accent_color",
                }
                attr = attr_map.get(key)
                if attr and hasattr(card, attr):
                    setattr(card, attr, value)
            else:
                if hasattr(card, key):
                    setattr(card, key, value)

        self.save_character(card)
        return card

    # ------------------------------------------------------------------
    # Reference images + embeddings
    # ------------------------------------------------------------------

    def add_reference_image(
        self,
        character_id: str,
        image_path: str,
        view: str = "front",
    ) -> str:
        """Add a reference image to a character and extract its embedding.

        The image is copied into the character's reference directory and
        a visual embedding is extracted and stored on the card.

        Args:
            character_id: The character's unique ID.
            image_path: Path to the reference image file.
            view: One of ``"front"``, ``"side"``, ``"back"``.

        Returns:
            The stored reference image path.
        """
        import shutil

        card = self.load_character(character_id)
        src = Path(image_path)
        if not src.exists():
            raise FileNotFoundError(f"Reference image not found: {image_path}")

        ref_dir = self._references_dir(character_id)
        dest = ref_dir / f"{view}_{src.name}"
        shutil.copy2(src, dest)

        # Set the appropriate view path
        view_attr = f"{view}_view_path"
        if hasattr(card, view_attr):
            setattr(card, view_attr, str(dest))

        # Extract and store embedding
        try:
            img = Image.open(dest).convert("RGB")
            embedding = self.extract_embedding(img)
            card.visual_embedding = embedding
            log.info(f"Extracted {len(embedding)}-dim embedding for {character_id}")
        except Exception as e:
            log.warning(f"Embedding extraction failed: {e}")

        self.save_character(card)
        return str(dest)

    def extract_embedding(self, image: Image.Image) -> List[float]:
        """Extract a visual embedding from *image*.

        Uses CLIP if available, otherwise falls back to a histogram.

        Args:
            image: A PIL Image.

        Returns:
            Embedding vector as list of floats.
        """
        return self._extractor.extract(image)

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    def get_generation_prompt(self, character_id: str, base_prompt: str = "") -> str:
        """Build a full generation prompt with character consistency.

        The character's style prompt suffix is appended to *base_prompt*
        to lock appearance.

        Args:
            character_id: The character's unique ID.
            base_prompt: User-provided prompt describing the scene/pose.

        Returns:
            Combined prompt string.
        """
        card = self.load_character(character_id)
        style_suffix = card.generate_style_prompt()

        if base_prompt:
            return f"{base_prompt}, {style_suffix}"
        return style_suffix

    def find_similar_characters(
        self,
        embedding: List[float],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Find characters whose visual embedding is closest to *embedding*.

        Args:
            embedding: Query embedding vector.
            top_k: Number of results to return.

        Returns:
            List of dicts with character_id, name, similarity score,
            sorted by descending similarity.
        """
        results: List[Dict[str, Any]] = []

        for json_file in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                card_emb = data.get("embedding")
                if not card_emb:
                    continue
                sim = self._extractor.similarity(embedding, card_emb)
                results.append({
                    "character_id": data.get("character_id"),
                    "name": data.get("name"),
                    "similarity": round(sim, 4),
                })
            except Exception as e:
                log.debug(f"Error comparing {json_file}: {e}")

        results.sort(key=lambda r: r["similarity"], reverse=True)
        return results[:top_k]
