from pydantic import BaseModel


class FavoriteCreate(BaseModel):
    """Payload the Flutter client sends when saving a recipe."""
    recipe_title: str
    recipe_data: dict


class FavoriteResponse(BaseModel):
    """
    Shape returned by POST /favorites and each item in GET /favorites.

    - id          → used by Flutter to call DELETE /favorites/{id}
    - recipe_title → displayed in list views without deserialising recipe_data
    - recipe_data  → full recipe dict; Flutter reconstructs a Recipe object from it
    """
    id: int
    recipe_title: str
    recipe_data: dict

    model_config = {"from_attributes": True}
