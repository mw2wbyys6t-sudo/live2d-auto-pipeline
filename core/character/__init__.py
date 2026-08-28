#!/usr/bin/env python3
"""
Live2D Master Agent - Character Consistency System

Provides character card data model, CRUD management, and visual embedding
extraction for maintaining character consistency across generations.
"""

from core.character.card import CharacterCard
from core.character.manager import CharacterManager
from core.character.embedding import EmbeddingExtractor

__all__ = ["CharacterCard", "CharacterManager", "EmbeddingExtractor"]
