from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from PIL import Image
import os
from openai import OpenAI
import io
import json
import base64

# Lê API key da variável de ambiente
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

app = FastAPI(title="Smart Kitchen API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health_check():
    return {"status": "API running"}

def detect_ingredients_from_image(image_bytes: bytes, language: str = "pt") -> List[str]:
    """
    Detecta ingredientes reais numa imagem usando GPT-4o-mini.
    - Ignora objetos que não sejam comida
    - Suporta português e inglês
    """
    try:
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        lang_text = "Portuguese" if language == "pt" else "English"

        prompt = (
            f"You are a smart cooking assistant. "
            f"Analyze the following image (base64) and list ONLY edible ingredients. "
            f"Ignore anything that is not food. Respond in {lang_text}. "
            f"Return a simple comma-separated list.\n\n"
            f"{img_b64}"
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.6,
        )

        content = response.choices[0].message.content.strip()

        # separar por vírgula e limpar espaços
        ingredients = [i.strip().lower() for i in content.split(",") if i.strip()]

        if not ingredients:
            return ["ingredientes não detectados"]

        return ingredients

    except Exception as e:
        print("Erro detectando ingredientes:", e)
        return ["ingredientes não detectados"]

def generate_recipes(ingredients: List[str], language: str):
    lang = "Portuguese" if language == "pt" else "English"

    prompt = f"""
You are a cooking assistant.

Available ingredients:
{", ".join(ingredients)}

Rules:
- Use only the listed ingredients
- Maximum cooking time: 30 minutes
- Simple, practical home cooking
- Respond in {lang}
- Output ONLY valid JSON

JSON format:
{{
  "recipes": [
    {{
      "title": "",
      "time_minutes": 0,
      "steps": []
    }}
  ]
}}

Generate 3 recipes.
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
    )

    content = response.choices[0].message.content.strip()

    # Remover blocos ```json e ```
    if content.startswith("```json"):
        content = content[len("```json"):].strip()
    if content.endswith("```"):
        content = content[:-3].strip()

    # Agora parseia para JSON
    return json.loads(content)




@app.post("/analyze-image/")
async def analyze_image(file: UploadFile = File(...), language: str = "pt"):
    try:
        # Ler a imagem
        image_bytes = await file.read()
        Image.open(io.BytesIO(image_bytes))  # validação básica de imagem

        # Detectar ingredientes
        ingredients = detect_ingredients_from_image(image_bytes, language)

        # Gerar receitas
        recipes_data = generate_recipes(ingredients, language)

        # Garantir que é JSON válido
        if not isinstance(recipes_data, dict) or "recipes" not in recipes_data:
            return {
                "error": "Invalid JSON from LLM",
                "raw_response": str(recipes_data)
            }

        return {
            "ingredients_detected": ingredients,
            "recipes": recipes_data["recipes"]
        }

    except Exception as e:
        # Captura qualquer erro interno e mostra
        if "rate_limit" in str(e).lower():
            return {
                "error": "Rate limit atingido",
                "message": "Muitas análises seguidas. Tenta novamente em alguns segundos."
            }
        return {
            "error": "Internal Server Error",
            "details": str(e)
        }