# Unicode normalization utilities
import unicodedata

# ========================
# NORMALIZATION MAP
# ========================

# Mapping of ingredient variations to a canonical form
# Used to standardize inputs and improve consistency
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
        Normalizes raw text input.

        Steps:
        - Converts to lowercase
        - Removes leading/trailing spaces
        - Applies Unicode normalization (NFKD)

        This ensures consistent comparison across different
        languages, accents, and input formats.
    """

    text = text.lower().strip()

    # Normalize unicode characters (e.g., accents)
    text = unicodedata.normalize("NFKD", text)

    # REMOVE ACCENTS
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
        Normalizes a list of ingredient names.

        Process:
        1. Normalize each ingredient string
        2. Map known variations to canonical names
        3. Remove duplicates
        4. Return sorted list

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
