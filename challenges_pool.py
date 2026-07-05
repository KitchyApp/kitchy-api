"""
CHALLENGE_POOL
==============
Static catalogue of Chef Challenges used by the weekly rotation scheduler.

Structure
---------
Each entry is a dict with:
  title                : Display name shown in the Flutter UI.
  required_ingredients : Comma-separated, lowercase, diacritic-stripped slugs.
                         The /challenges/{id}/verify endpoint uses partial-match
                         normalised comparison, so "grao-de-bico" matches
                         "grão-de-bico cozido" automatically.
  is_premium_only      : True  → only Premium subscribers can attempt.
                         False → available to all users.
  badge_code           : Short identifier used as the badge key in the UI.

Rotation contract
-----------------
  • 1 challenge with is_premium_only=False  is selected per week.
  • 2 challenges with is_premium_only=True  are selected per week.
  • random.sample() is used so consecutive weeks are unlikely to repeat.

To add more challenges: append a new dict to FREE_CHALLENGES or
PREMIUM_CHALLENGES and restart the server. The next rotation will include
the new entry in the random pool.
"""

# ── Free challenges (available to all plans) ──────────────────────────────────

FREE_CHALLENGES: list[dict] = [
    {
        "title": "Rei do Tomate & Manjericão",
        "required_ingredients": "tomate,manjericao",
        "is_premium_only": False,
        "badge_code": "badge_tomato",
    },
    {
        "title": "Mestre das Leguminosas",
        "required_ingredients": "grao-de-bico,espinafres",
        "is_premium_only": False,
        "badge_code": "badge_chickpea",
    },
    {
        "title": "Caçador de Atum",
        "required_ingredients": "atum,grao-de-bico",
        "is_premium_only": False,
        "badge_code": "badge_tuna",
    },
    {
        "title": "Herói das Lentilhas",
        "required_ingredients": "lentilhas,cenoura",
        "is_premium_only": False,
        "badge_code": "badge_lentil",
    },
    {
        "title": "Rei dos Ovos",
        "required_ingredients": "ovos,tomate",
        "is_premium_only": False,
        "badge_code": "badge_egg",
    },
]

# ── Premium challenges (Premium subscribers only) ─────────────────────────────

PREMIUM_CHALLENGES: list[dict] = [
    {
        "title": "Monstro do Ginásio",
        "required_ingredients": "frango,ovos",
        "is_premium_only": True,
        "badge_code": "badge_protein",
    },
    {
        "title": "Chef de Elite",
        "required_ingredients": "salmao,abacate",
        "is_premium_only": True,
        "badge_code": "badge_gourmet",
    },
    {
        "title": "Lombo & Alecrim",
        "required_ingredients": "lombo de porco,alecrim",
        "is_premium_only": True,
        "badge_code": "badge_rosemary",
    },
    {
        "title": "Rei do Mediterrâneo",
        "required_ingredients": "bacalhau,azeitonas,tomate",
        "is_premium_only": True,
        "badge_code": "badge_mediterranean",
    },
    {
        "title": "O Cogumelo Místico",
        "required_ingredients": "cogumelos,carne picada",
        "is_premium_only": True,
        "badge_code": "badge_mushroom",
    },
]

# ── Unified pool (used by code that needs the full catalogue) ─────────────────

CHALLENGE_POOL: list[dict] = FREE_CHALLENGES + PREMIUM_CHALLENGES
