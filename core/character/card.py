#!/usr/bin/env python3
"""
Live2D Master Agent - Character Card Data Model

Defines the CharacterCard dataclass that stores all visual and persona
parameters needed to maintain character consistency across generations.
"""

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_uuid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


# Face parameter vector fields used for similarity computation.
FACE_VECTOR_FIELDS: List[str] = [
    "face_width_ratio",
    "face_height_ratio",
    "eye_size",
]

# Enumerations for validation / prompt generation.
FACE_SHAPES = ("oval", "round", "slim")
HAIR_STYLES = ("long", "short", "twintail", "ponytail", "bob")
BODY_TYPES = ("slim", "average", "curvy")


class CharacterCard:
    """Character card that captures all consistency parameters.

    A CharacterCard is a serializable snapshot of a character's visual
    design and persona. It can be saved to / loaded from JSON and used
    to generate style-locked prompts for image generation.
    """

    def __init__(
        self,
        character_id: Optional[str] = None,
        name: str = "Unnamed",
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        # Face
        face_shape: str = "oval",
        eye_shape: str = "large_anime",
        eye_color: str = "#4a90d9",
        eye_size: float = 1.0,
        eyebrow_shape: str = "soft_arch",
        nose_type: str = "small",
        mouth_type: str = "small_smile",
        face_width_ratio: float = 1.0,
        face_height_ratio: float = 1.0,
        # Hair
        hair_color: str = "#3a2a1a",
        hair_style: str = "long",
        hair_length: float = 1.0,
        bangs_style: str = "straight",
        highlights_color: str = "#8b6f4e",
        # Body
        body_type: str = "slim",
        height_ratio: float = 1.0,
        proportions: str = "anime_standard",
        # Color palette
        primary_colors: Optional[List[str]] = None,
        skin_tone: str = "#f5d5b8",
        accent_color: str = "#e74c3c",
        # Outfit
        current_outfit: Optional[Dict[str, Any]] = None,
        wardrobe: Optional[List[Dict[str, Any]]] = None,
        # Reference images
        front_view_path: Optional[str] = None,
        side_view_path: Optional[str] = None,
        back_view_path: Optional[str] = None,
        # Embedding
        visual_embedding: Optional[List[float]] = None,
        # Persona
        personality: str = "",
        voice_style: str = "",
        backstory: str = "",
        # Style prompt constraints
        style_constraints: str = "",
        negative_prompt: str = "",
    ) -> None:
        """Initialize a CharacterCard with sensible anime defaults."""
        self.character_id: str = character_id or _new_uuid()
        self.name: str = name
        self.created_at: str = created_at or _now_iso()
        self.updated_at: str = updated_at or self.created_at

        # Face parameters
        self.face_shape: str = face_shape
        self.eye_shape: str = eye_shape
        self.eye_color: str = eye_color
        self.eye_size: float = float(eye_size)
        self.eyebrow_shape: str = eyebrow_shape
        self.nose_type: str = nose_type
        self.mouth_type: str = mouth_type
        self.face_width_ratio: float = float(face_width_ratio)
        self.face_height_ratio: float = float(face_height_ratio)

        # Hair parameters
        self.hair_color: str = hair_color
        self.hair_style: str = hair_style
        self.hair_length: float = float(hair_length)
        self.bangs_style: str = bangs_style
        self.highlights_color: str = highlights_color

        # Body parameters
        self.body_type: str = body_type
        self.height_ratio: float = float(height_ratio)
        self.proportions: str = proportions

        # Color palette
        self.primary_colors: List[str] = primary_colors if primary_colors is not None else []
        self.skin_tone: str = skin_tone
        self.accent_color: str = accent_color

        # Outfit
        self.current_outfit: Dict[str, Any] = current_outfit if current_outfit is not None else {}
        self.wardrobe: List[Dict[str, Any]] = wardrobe if wardrobe is not None else []

        # Reference image paths
        self.front_view_path: Optional[str] = front_view_path
        self.side_view_path: Optional[str] = side_view_path
        self.back_view_path: Optional[str] = back_view_path

        # Visual embedding (from CLIP or histogram)
        self.visual_embedding: Optional[List[float]] = visual_embedding

        # Persona
        self.personality: str = personality
        self.voice_style: str = voice_style
        self.backstory: str = backstory

        # Style constraints
        self.style_constraints: str = style_constraints
        self.negative_prompt: str = negative_prompt

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the card to a JSON-compatible dict.

        Returns:
            dict representation of the character card.
        """
        return {
            "character_id": self.character_id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "face": {
                "shape": self.face_shape,
                "eye_shape": self.eye_shape,
                "eye_color": self.eye_color,
                "eye_size": self.eye_size,
                "eyebrow_shape": self.eyebrow_shape,
                "nose_type": self.nose_type,
                "mouth_type": self.mouth_type,
                "face_width_ratio": self.face_width_ratio,
                "face_height_ratio": self.face_height_ratio,
            },
            "hair": {
                "color": self.hair_color,
                "style": self.hair_style,
                "length": self.hair_length,
                "bangs_style": self.bangs_style,
                "highlights_color": self.highlights_color,
            },
            "body": {
                "type": self.body_type,
                "height_ratio": self.height_ratio,
                "proportions": self.proportions,
            },
            "palette": {
                "primary_colors": list(self.primary_colors),
                "skin_tone": self.skin_tone,
                "accent_color": self.accent_color,
            },
            "outfit": {
                "current": dict(self.current_outfit),
                "wardrobe": [dict(o) for o in self.wardrobe],
            },
            "references": {
                "front": self.front_view_path,
                "side": self.side_view_path,
                "back": self.back_view_path,
            },
            "embedding": list(self.visual_embedding) if self.visual_embedding else None,
            "persona": {
                "personality": self.personality,
                "voice_style": self.voice_style,
                "backstory": self.backstory,
            },
            "style": {
                "constraints": self.style_constraints,
                "negative_prompt": self.negative_prompt,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CharacterCard":
        """Deserialize a CharacterCard from a dict.

        Args:
            data: dict previously produced by :meth:`to_dict`.

        Returns:
            A new CharacterCard instance.
        """
        face = data.get("face", {})
        hair = data.get("hair", {})
        body = data.get("body", {})
        palette = data.get("palette", {})
        outfit = data.get("outfit", {})
        refs = data.get("references", {})
        persona = data.get("persona", {})
        style = data.get("style", {})

        return cls(
            character_id=data.get("character_id"),
            name=data.get("name", "Unnamed"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            # Face
            face_shape=face.get("shape", "oval"),
            eye_shape=face.get("eye_shape", "large_anime"),
            eye_color=face.get("eye_color", "#4a90d9"),
            eye_size=face.get("eye_size", 1.0),
            eyebrow_shape=face.get("eyebrow_shape", "soft_arch"),
            nose_type=face.get("nose_type", "small"),
            mouth_type=face.get("mouth_type", "small_smile"),
            face_width_ratio=face.get("face_width_ratio", 1.0),
            face_height_ratio=face.get("face_height_ratio", 1.0),
            # Hair
            hair_color=hair.get("color", "#3a2a1a"),
            hair_style=hair.get("style", "long"),
            hair_length=hair.get("length", 1.0),
            bangs_style=hair.get("bangs_style", "straight"),
            highlights_color=hair.get("highlights_color", "#8b6f4e"),
            # Body
            body_type=body.get("type", "slim"),
            height_ratio=body.get("height_ratio", 1.0),
            proportions=body.get("proportions", "anime_standard"),
            # Palette
            primary_colors=palette.get("primary_colors", []),
            skin_tone=palette.get("skin_tone", "#f5d5b8"),
            accent_color=palette.get("accent_color", "#e74c3c"),
            # Outfit
            current_outfit=outfit.get("current", {}),
            wardrobe=outfit.get("wardrobe", []),
            # References
            front_view_path=refs.get("front"),
            side_view_path=refs.get("side"),
            back_view_path=refs.get("back"),
            # Embedding
            visual_embedding=data.get("embedding"),
            # Persona
            personality=persona.get("personality", ""),
            voice_style=persona.get("voice_style", ""),
            backstory=persona.get("backstory", ""),
            # Style
            style_constraints=style.get("constraints", ""),
            negative_prompt=style.get("negative_prompt", ""),
        )

    def save(self, path: str) -> str:
        """Save the card as JSON to *path*.

        Args:
            path: Destination file path. Parent directories are created.

        Returns:
            The absolute path the card was written to.
        """
        self.updated_at = _now_iso()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(p.resolve())

    @classmethod
    def load(cls, path: str) -> "CharacterCard":
        """Load a CharacterCard from a JSON file.

        Args:
            path: Path to a JSON file previously written by :meth:`save`.

        Returns:
            A CharacterCard instance.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    # ------------------------------------------------------------------
    # Prompt generation
    # ------------------------------------------------------------------

    def generate_style_prompt(self) -> str:
        """Generate a prompt suffix that locks character consistency.

        The returned string is meant to be appended to the base image
        generation prompt to anchor the character's appearance.

        Returns:
            A comma-separated string of consistency descriptors.
        """
        parts: List[str] = []

        # Face descriptors
        parts.append(f"{self.face_shape} face")
        parts.append(f"{self.eye_shape} {self.eye_color} eyes")
        if self.eye_size != 1.0:
            parts.append(f"eye size {self.eye_size:.2f}")
        parts.append(f"{self.eyebrow_shape} eyebrows")

        # Hair descriptors
        parts.append(f"{self.hair_color} {self.hair_style} hair")
        parts.append(f"{self.bangs_style} bangs")
        if self.highlights_color:
            parts.append(f"{self.highlights_color} hair highlights")

        # Body descriptors
        parts.append(f"{self.body_type} body")
        parts.append(f"{self.proportions} proportions")

        # Palette
        if self.skin_tone:
            parts.append(f"{self.skin_tone} skin tone")
        if self.primary_colors:
            parts.append("primary colors " + ", ".join(self.primary_colors[:4]))
        if self.accent_color:
            parts.append(f"accent color {self.accent_color}")

        # Outfit (high-level)
        if self.current_outfit:
            top = self.current_outfit.get("top") or self.current_outfit.get("description")
            if top:
                parts.append(str(top))

        # User-defined style constraints
        if self.style_constraints:
            parts.append(self.style_constraints)

        # Character name anchor
        parts.append(f"character sheet of {self.name}")
        parts.append("consistent character design, same character, reference sheet")

        return ", ".join(p for p in parts if p)

    def generate_negative_prompt(self) -> str:
        """Generate a negative prompt to prevent character drift.

        Returns:
            A comma-separated negative prompt string.
        """
        defaults = [
            "different character", "inconsistent design", "multiple characters",
            "mutated face", "asymmetrical eyes", "bad anatomy",
            "extra limbs", "deformed", "low quality", "blurry",
            "wrong hair color", "wrong eye color",
        ]
        if self.negative_prompt:
            defaults.insert(0, self.negative_prompt)
        return ", ".join(defaults)

    # ------------------------------------------------------------------
    # Iterative refinement
    # ------------------------------------------------------------------

    def merge_from_generation(self, generated_card: "CharacterCard", weight: float = 0.3) -> None:
        """Blend parameters from a newly generated card into this one.

        This supports iterative refinement: after generating a new image
        and extracting parameters, the new values are blended with the
        existing card using *weight* (0 = keep existing, 1 = use new).

        Args:
            generated_card: Card extracted from the latest generation.
            weight: Blend weight for the new parameters (0.0-1.0).
        """
        weight = max(0.0, min(1.0, weight))

        def blend_numeric(old: float, new: float) -> float:
            return old * (1.0 - weight) + new * weight

        # Numeric face parameters
        self.eye_size = blend_numeric(self.eye_size, generated_card.eye_size)
        self.face_width_ratio = blend_numeric(self.face_width_ratio, generated_card.face_width_ratio)
        self.face_height_ratio = blend_numeric(self.face_height_ratio, generated_card.face_height_ratio)

        # Numeric hair/body parameters
        self.hair_length = blend_numeric(self.hair_length, generated_card.hair_length)
        self.height_ratio = blend_numeric(self.height_ratio, generated_card.height_ratio)

        # Categorical parameters: take the generated value with probability = weight
        import random
        if random.random() < weight:
            self.face_shape = generated_card.face_shape
            self.eye_shape = generated_card.eye_shape
            self.eye_color = generated_card.eye_color
            self.hair_color = generated_card.hair_color
            self.hair_style = generated_card.hair_style
            self.body_type = generated_card.body_type
            self.skin_tone = generated_card.skin_tone

        # Merge embeddings if both present (weighted average)
        if self.visual_embedding and generated_card.visual_embedding:
            if len(self.visual_embedding) == len(generated_card.visual_embedding):
                self.visual_embedding = [
                    blend_numeric(a, b)
                    for a, b in zip(self.visual_embedding, generated_card.visual_embedding)
                ]
        elif generated_card.visual_embedding:
            self.visual_embedding = list(generated_card.visual_embedding)

        self.updated_at = _now_iso()

    # ------------------------------------------------------------------
    # Similarity
    # ------------------------------------------------------------------

    def _face_vector(self) -> List[float]:
        """Build a numeric face parameter vector for similarity."""
        # One-hot encode categorical face parameters
        vec: List[float] = []

        # face_shape one-hot
        for shape in FACE_SHAPES:
            vec.append(1.0 if self.face_shape == shape else 0.0)

        # eye_shape encoded as simple hash bucket (deterministic)
        vec.append(float(hash(self.eye_shape) % 100) / 100.0)

        # eye_color: approximate as RGB normalized
        try:
            hex_color = self.eye_color.lstrip("#")
            r = int(hex_color[0:2], 16) / 255.0
            g = int(hex_color[2:4], 16) / 255.0
            b = int(hex_color[4:6], 16) / 255.0
            vec.extend([r, g, b])
        except (ValueError, IndexError):
            vec.extend([0.0, 0.0, 0.0])

        # Numeric face params
        vec.append(self.eye_size)
        vec.append(self.face_width_ratio)
        vec.append(self.face_height_ratio)

        # Normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def compute_face_similarity(self, other: "CharacterCard") -> float:
        """Compute cosine similarity between face parameter vectors.

        Args:
            other: Another CharacterCard to compare against.

        Returns:
            Cosine similarity in [-1, 1]; closer to 1 means more similar.
        """
        v1 = self._face_vector()
        v2 = other._face_vector()
        dot = sum(a * b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a * a for a in v1)) or 1.0
        n2 = math.sqrt(sum(b * b for b in v2)) or 1.0
        return dot / (n1 * n2)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CharacterCard(id={self.character_id[:8]}..., name={self.name!r}, "
            f"hair={self.hair_color}, eyes={self.eye_color})"
        )
