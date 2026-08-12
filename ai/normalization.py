"""Ingredient text normalisation for consistent cache keys and AI prompts."""

# Unicode normalization utilities
import unicodedata

# ========================
# NORMALIZATION MAP
# ========================

# Mapping of ingredient variations to a canonical form.
# Used to standardize inputs and improve consistency.
# Example: "courgette" → "zucchini"
NORMALIZATION_MAP = {
    "tomatoes": "tomato",
    "cherry tomato": "tomato",
    "zucchini": "zucchini",
    "courgette": "zucchini",
    "curgete": "zucchini",
    "white fish": "fish",
    "salmon": "fish",
    "pescada": "fish",
    "solha": "fish",
}


# ========================
# TEXT NORMALIZATION
# ========================

def normalize_text(text: str) -> str:
    """
    Lowercase, trim, and strip accents from a raw ingredient string.

    Applies Unicode NFKD normalisation so comparisons are stable across
    languages, accents, and inconsistent user input.
    """

    text = text.lower().strip()

    # Normalize unicode characters (e.g., accents)
    text = unicodedata.normalize("NFKD", text)

    # Remove combining marks (accents) left after NFKD decomposition.
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )

    return text


# ========================
# INGREDIENT NORMALIZATION
# ========================

def normalize_ingredients(ingredients: list[str]) -> list[str]:
    """
    Normalise a list of ingredient names to canonical, deduplicated form.

    Process:
    1. Normalise each ingredient string via normalize_text()
    2. Map known variations to canonical names via NORMALIZATION_MAP
    3. Remove duplicates
    4. Return a sorted list

    Example:
        ["Tomatoes", "cherry tomato"] → ["tomato"]
    """

    normalized = []

    for item in ingredients:
        # Normalize raw text
        item = normalize_text(item)

        # Map to canonical ingredient if exists
        if item in NORMALIZATION_MAP:
            item = NORMALIZATION_MAP[item]

        normalized.append(item)

    # Remove duplicates and sort alphabetically
    return sorted(list(set(normalized)))
