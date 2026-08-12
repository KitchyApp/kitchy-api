"""
CHALLENGE_POOL
==============
Static catalogue of Chef Challenges used by the weekly rotation scheduler.

Structure
---------
Each entry is a dict with:
  title                : Display name shown in the Flutter UI.
  required_ingredients : Comma-separated, lowercase, diacritic-stripped slugs.
  is_premium_only      : True  → only Premium subscribers can attempt.
  badge_code           : Short identifier used as the badge key in the UI.
  category             : "culinary" | "barman" — Flutter Challenges TabBar.

Rotation contract
-----------------
  • Culinary: 1 free + 2 premium per week.
  • Barman:   1 free + 1 premium per week.
"""

# ── Culinary — free ───────────────────────────────────────────────────────────
# Rotated weekly: exactly 1 entry from this list is active per week (see scheduler).

FREE_CULINARY_CHALLENGES: list[dict] = [
    {
        "title": "Rei do Tomate & Manjericão",
        "required_ingredients": "tomate,manjericao",
        "is_premium_only": False,
        "badge_code": "badge_tomato",
        "category": "culinary",
    },
    {
        "title": "Mestre das Leguminosas",
        "required_ingredients": "grao-de-bico,espinafres",
        "is_premium_only": False,
        "badge_code": "badge_chickpea",
        "category": "culinary",
    },
    {
        "title": "Caçador de Atum",
        "required_ingredients": "atum,grao-de-bico",
        "is_premium_only": False,
        "badge_code": "badge_tuna",
        "category": "culinary",
    },
    {
        "title": "Herói das Lentilhas",
        "required_ingredients": "lentilhas,cenoura",
        "is_premium_only": False,
        "badge_code": "badge_lentil",
        "category": "culinary",
    },
    {
        "title": "Rei dos Ovos",
        "required_ingredients": "ovos,tomate",
        "is_premium_only": False,
        "badge_code": "badge_egg",
        "category": "culinary",
    },
]

# ── Culinary — premium ────────────────────────────────────────────────────────
# Rotated weekly: exactly 2 entries from this list are active per week.

PREMIUM_CULINARY_CHALLENGES: list[dict] = [
    {
        "title": "Monstro do Ginásio",
        "required_ingredients": "frango,ovos",
        "is_premium_only": True,
        "badge_code": "badge_protein",
        "category": "culinary",
    },
    {
        "title": "Chef de Elite",
        "required_ingredients": "salmao,abacate",
        "is_premium_only": True,
        "badge_code": "badge_gourmet",
        "category": "culinary",
    },
    {
        "title": "Lombo & Alecrim",
        "required_ingredients": "lombo de porco,alecrim",
        "is_premium_only": True,
        "badge_code": "badge_rosemary",
        "category": "culinary",
    },
    {
        "title": "Rei do Mediterrâneo",
        "required_ingredients": "bacalhau,azeitonas,tomate",
        "is_premium_only": True,
        "badge_code": "badge_mediterranean",
        "category": "culinary",
    },
    {
        "title": "O Cogumelo Místico",
        "required_ingredients": "cogumelos,carne picada",
        "is_premium_only": True,
        "badge_code": "badge_mushroom",
        "category": "culinary",
    },
]

# ── Barman — free ─────────────────────────────────────────────────────────────
# Rotated weekly: exactly 1 entry from this list is active per week.

FREE_BARMAN_CHALLENGES: list[dict] = [
    {
        "title": "Mestre do Gin Tónico",
        "required_ingredients": "gin,tonic",
        "is_premium_only": False,
        "badge_code": "badge_gin_tonic",
        "category": "barman",
    },
    {
        "title": "Rei do Mojito",
        "required_ingredients": "rum,hortela,limao",
        "is_premium_only": False,
        "badge_code": "badge_mojito",
        "category": "barman",
    },
    {
        "title": "Caçador de Citrus",
        "required_ingredients": "vodka,limao",
        "is_premium_only": False,
        "badge_code": "badge_citrus",
        "category": "barman",
    },
]

# ── Barman — premium ──────────────────────────────────────────────────────────
# Rotated weekly: exactly 1 entry from this list is active per week.

PREMIUM_BARMAN_CHALLENGES: list[dict] = [
    {
        "title": "Negroni Classico",
        "required_ingredients": "gin,campari,vermut",
        "is_premium_only": True,
        "badge_code": "badge_negroni",
        "category": "barman",
    },
    {
        "title": "Bartender de Elite",
        "required_ingredients": "whisky,bitters",
        "is_premium_only": True,
        "badge_code": "badge_whisky",
        "category": "barman",
    },
    {
        "title": "Margarita Master",
        "required_ingredients": "tequila,limao",
        "is_premium_only": True,
        "badge_code": "badge_margarita",
        "category": "barman",
    },
]

# ── Backward-compatible aliases (used by older imports) ───────────────────────

FREE_CHALLENGES: list[dict] = FREE_CULINARY_CHALLENGES
PREMIUM_CHALLENGES: list[dict] = PREMIUM_CULINARY_CHALLENGES

# Flat union of all challenge dicts — used for seeding DB rows and bulk lookups.
CHALLENGE_POOL: list[dict] = (
    FREE_CULINARY_CHALLENGES
    + PREMIUM_CULINARY_CHALLENGES
    + FREE_BARMAN_CHALLENGES
    + PREMIUM_BARMAN_CHALLENGES
)
