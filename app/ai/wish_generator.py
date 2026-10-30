"""
WishGenerator orchestrates prompt construction and AI generation for birthday wishes.
"""
from typing import Any, Dict, Optional, Tuple

from app.ai.client import GeminiClient
from app.ai.prompts import build_wish_prompt, normalize_style


class WishGenerator:
    """Orchestrates personalized birthday wish generation using GeminiClient."""

    def __init__(self, client: Optional[GeminiClient] = None) -> None:
        self.client = client or GeminiClient()

    async def generate_birthday_wish(
        self,
        friend_profile: Dict[str, Any],
        style_override: Optional[str] = None,
        variation_seed: Optional[int] = None,
    ) -> Tuple[str, str]:
        """
        Generate a personalized birthday wish for a friend.

        Args:
            friend_profile: Dictionary containing friend details:
                - name (str): Friend's name.
                - relationship (Optional[str]): Relationship descriptor.
                - interests (Optional[str]): Hobbies or interests.
                - notes (Optional[str]): Personal notes.
                - wish_style (Optional[str]): Preferred style preset.
            style_override: Explicit style choice from user (overrides profile preference).
            variation_seed: Seed to introduce variation when regenerating.

        Returns:
            Tuple of (generated_wish_text, canonical_style_used).
        """
        name = friend_profile.get("name", "Friend")
        relationship = friend_profile.get("relationship")
        interests = friend_profile.get("interests")
        notes = friend_profile.get("notes")

        # Determine effective style: override > profile style > Friendly
        chosen_style = style_override or friend_profile.get("wish_style")
        canonical_style = normalize_style(chosen_style)

        # Build prompt and system instruction with variation angle if regenerating
        prompt, system_instruction, final_style = build_wish_prompt(
            name=name,
            relationship=relationship,
            interests=interests,
            notes=notes,
            wish_style=canonical_style,
            variation_seed=variation_seed,
        )

        wish_text = await self.client.generate_text(
            prompt=prompt,
            system_instruction=system_instruction,
        )

        return wish_text, final_style
