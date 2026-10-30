"""
AI prompt templates and style definitions for personalized birthday wish generation.
"""
from typing import Dict, Optional, Tuple

SUPPORTED_STYLES: Dict[str, str] = {
    "Friendly": "Casual, warm, upbeat, and cheerful, like a genuine message from a good friend.",
    "Funny": "Playful, witty, and lighthearted with friendly humor without being mean or insulting.",
    "Short and simple": "Concise, sweet, punchy (1 to 2 sentences maximum), straight to the point.",
    "Formal": "Polite, respectful, elegant, well-mannered, and professional.",
    "Warm and heartfelt": "Deeply sincere, appreciative, emotionally meaningful, celebrating the personal bond.",
    "Casual": "Relaxed, easygoing, conversational, natural, and low-key.",
    "Poetic": "Lyrical, expressive, rhythmic, artistic, and evocative.",
}

# Mapping of profile style presets or variations to standard canonical styles
STYLE_MAPPINGS: Dict[str, str] = {
    "friendly": "Friendly",
    "funny": "Funny",
    "short and simple": "Short and simple",
    "short & simple": "Short and simple",
    "formal": "Formal",
    "warm and heartfelt": "Warm and heartfelt",
    "warm & heartfelt": "Warm and heartfelt",
    "casual": "Casual",
    "casual & friendly": "Casual",
    "poetic": "Poetic",
    "poetic & creative": "Poetic",
    "funny & humorous": "Funny",
    "formal & polite": "Formal",
}

SYSTEM_INSTRUCTION = (
    "You are BirthdayMate, an empathetic, thoughtful, and creative AI birthday assistant. "
    "Your purpose is to compose a personalized, authentic birthday wish for a friend, family member, or colleague.\n\n"
    "CRITICAL RULES:\n"
    "1. Strict Truthfulness: Rely ONLY on the personal details explicitly provided (name, relationship, interests, personal notes). "
    "Never make up, invent, or assume unprovided memories, hobbies, inside jokes, or factual claims.\n"
    "2. Missing Profile Handling: If any fields (relationship, interests, personal notes) are missing or empty, "
    "still write a natural, sincere birthday wish using only what is available. Do not mention missing details or say 'as you requested'.\n"
    "3. Style Fidelity: Strictly embody the requested tone/style.\n"
    "4. Natural Phrasing: Avoid unnecessarily repeating the friend's name multiple times. Address them naturally.\n"
    "5. Pure Output: Output ONLY the text of the birthday wish. Do not include markdown quotes, greeting headers, preambles, explanations, or metadata."
)


VARIATION_ANGLES = [
    "Focus on celebrating their positive energy, future aspirations, and the exciting year ahead.",
    "Focus on expressing genuine gratitude, warmth, and appreciation for having them in your life.",
    "Craft an inspiring and uplifting message that is memorable and encouraging.",
    "Take a fresh, lively, and creative angle with distinct sentence phrasing.",
    "Highlight their unique personality and wish them lasting happiness, health, and success.",
]


def normalize_style(raw_style: Optional[str]) -> str:
    """
    Map an input style or profile preset to one of the canonical supported styles.
    Defaults to 'Friendly' if not recognized or not provided.
    """
    if not raw_style or not raw_style.strip():
        return "Friendly"

    cleaned = raw_style.strip().lower()
    if cleaned in STYLE_MAPPINGS:
        return STYLE_MAPPINGS[cleaned]

    # Check for direct key match in SUPPORTED_STYLES (case-insensitive)
    for style in SUPPORTED_STYLES:
        if style.lower() == cleaned:
            return style

    # If it's a custom style entered by the user, return as capitalized custom style
    return raw_style.strip().capitalize()


def build_wish_prompt(
    name: str,
    relationship: Optional[str] = None,
    interests: Optional[str] = None,
    notes: Optional[str] = None,
    wish_style: Optional[str] = None,
    variation_seed: Optional[int] = None,
) -> Tuple[str, str, str]:
    """
    Construct user prompt and system instruction for generating a personalized birthday wish.

    Args:
        name: Friend's name.
        relationship: Optional relationship (e.g., 'Friend', 'Colleague', 'Classmate').
        interests: Optional hobbies/interests (e.g., 'Photography, hiking, jazz music').
        notes: Optional personal notes/memories (e.g., 'Helped me move last summer').
        wish_style: Optional desired style/tone.
        variation_seed: Optional integer or identifier to produce an alternate variation.

    Returns:
        Tuple of (user_prompt, system_instruction, canonical_style).
    """
    canonical_style = normalize_style(wish_style)
    style_desc = SUPPORTED_STYLES.get(
        canonical_style,
        f"Tone and manner matching: {canonical_style}",
    )

    details = [f"- Recipient Name: {name}"]

    if relationship and relationship.strip():
        details.append(f"- Relationship to Sender: {relationship.strip()}")

    if interests and interests.strip():
        details.append(f"- Interests & Passions: {interests.strip()}")

    if notes and notes.strip():
        details.append(f"- Special Notes & Shared Context: {notes.strip()}")

    details.append(f"- Desired Style: {canonical_style} ({style_desc})")

    details_block = "\n".join(details)

    prompt = (
        f"Please write a personalized birthday wish based on the following recipient details:\n\n"
        f"{details_block}\n\n"
        "Guidelines:\n"
        "- Tailor the message to the requested style.\n"
        "- Naturally incorporate interests and notes if provided, without listing them mechanically.\n"
        "- If details are sparse, generate a heartfelt and natural wish without inventing fictitious facts.\n"
        "- Output strictly the message text without extra preamble, title, or commentary."
    )

    if variation_seed is not None:
        angle = VARIATION_ANGLES[variation_seed % len(VARIATION_ANGLES)]
        prompt += (
            f"\n- Alternate Creative Angle: {angle} "
            f"Use distinct wording and a fresh perspective from standard greetings (Variation #{variation_seed})."
        )

    return prompt, SYSTEM_INSTRUCTION, canonical_style
