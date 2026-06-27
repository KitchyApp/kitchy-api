from pydantic import BaseModel


class FavoriteCreate(BaseModel):
    recipe_title: str
    recipe_data: dict


class FavoriteResponse(BaseModel):
    id: int
    recipe_title: str
    recipe_data: dict

    class Config:
        from_attributes = True
