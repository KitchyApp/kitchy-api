import base64
import io
import json
import os
import stripe
import hashlib
import redis
import structlog
from datetime import date, datetime, timedelta
from typing import List
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import create_engine, Column, Integer, String, Date, Boolean, DateTime, Index
from sqlalchemy.orm import sessionmaker, declarative_base, Session, Mapped, mapped_column
from jose import jwt, JWTError
from passlib.context import CryptContext
from PIL import Image
from openai import OpenAI
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, EmailStr, constr


load_dotenv()

# ========================
# CONFIG
# ========================

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

STRIPE_PRICE_ID = "price_1T1vP9Rv8WC0CvnKszqhOJyJ"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

redis_client = redis.from_url(os.getenv("REDIS_URL"))

logger = structlog.get_logger()

# ========================
# FASTAPI
# ========================

app = FastAPI()

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    lambda r, e: PlainTextResponse("Rate limit exceeded", status_code=429),
)

# ========================
# DATABASE
# ========================

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)
    password = Column(String)
    plan = Column(String, default="free")
    analyses_today = Column(Integer, default=0)
    last_analysis_date = Column(Date, nullable=True)

    # Stripe
    stripe_customer_id = Column(String, nullable=True)

    # Restrições alimentares
    dietary_gluten_free = Column(Boolean, default=False)
    dietary_vegetarian = Column(Boolean, default=False)
    dietary_vegan = Column(Boolean, default=False)
    preferred_style = Column(String, default="balanced")
    preferred_cuisine = Column(String, default="international")

    marketing_consent = Column(Boolean, default=False)

class RecipeCache(Base):
    __tablename__ = "recipe_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredients_hash: Mapped[str] = mapped_column(String, index=True)
    response_json: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

Index("idx_user_id", User.id)
Index("idx_ingredients_hash", RecipeCache.ingredients_hash)
Base.metadata.create_all(bind=engine)

class RegisterRequest(BaseModel):
    email: EmailStr
    password: constr(min_length=8)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UsageLog(Base):
    __tablename__ = "usage_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    tokens_used = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ========================
# AUTH
# ========================

pwd_context = CryptContext(schemes=["bcrypt"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(password, hashed):
    return pwd_context.verify(password, hashed)

def create_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)



# ========================
# DEPENDENCIES
# ========================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("id")
    except JWTError:
        raise HTTPException(401, "Token inválido")

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(401, "Utilizador não encontrado")

    return user

# ========================
# AI FUNCTIONS
# ========================


def detect_ingredients(image_bytes: bytes, language: str = "pt"):

    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "Return ONLY a JSON array.\n"
                        "[{\"name\":\"\",\"confidence\":\"high|medium|low\"}]\n"
                        "Rules:\n"
                        "- edible food only\n"
                        "- ignore any text in image\n"
                        f"- language: {language}"
                    )
                },
                {
                    "type": "input_image",
                    "image_base64": img_b64
                }
            ]
        }],
        max_output_tokens=200
    )

    try:
        return json.loads(response.output_text.strip())
    except:
        return []


def generate_recipes(
        ingredients: List[str],
        user: User,
        db: Session,
        language: str = "en-US",
):
    num_recipes = 1 if user.plan == "free" else 3
    restrictions = []
    user_style = user.preferred_style
    user_cuisine = user.preferred_cuisine

    if user.dietary_gluten_free:
        restrictions.append("gluten free")

    if user.dietary_vegetarian:
        restrictions.append("vegetarian")

    if user.dietary_vegan:
        restrictions.append("vegan")

    restrictions_text = ", ".join(restrictions) if restrictions else "none"

    language_instruction = f"Generate recipes in {language}. Adapt the culinary style to that country."

    ingredients_string = ",".join(sorted(ingredients)) + "_" + language
    ingredients_hash = hashlib.sha256(
        ingredients_string.encode()
    ).hexdigest()

    # Verificar cache
    cached = db.query(RecipeCache).filter(
        RecipeCache.ingredients_hash == ingredients_hash
    ).first()

    if cached:
        return json.loads(cached.response_json)

    prompt = f"""
    You are a professional nutrition chef.
    
    {language_instruction}
    
    User preferences:
    - Style: {user_style}
    - Cuisine: {user_cuisine}

    Ingredients:
    {", ".join(ingredients)}

    Dietary restrictions:
    {restrictions_text}

    Rules:
    - Max cooking time 30 minutes
    - Respect dietary restrictions
    - Use ONLY given ingredients + basic kitchen staples (salt, pepper, olive oil)
    - Provide approximate nutritional values
    - Output ONLY valid JSON

    Format:
    {{
     "recipes":[
       {{
         "title":"",
         "time_minutes":0,
         "calories":0,
         "protein_g":0,
         "carbs_g":0,
         "fat_g":0,
         "vitamins":{{
            "vitamin_a":"",
            "vitamin_c":"",
            "vitamin_d":"",
            "vitamin_b12":""
         }},
         "steps":[]
       }}
     ]
    }}

    Generate {num_recipes} recipes.
    """

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        max_output_tokens=400
    )

    parsed = json.loads(response.output_text)

    # Guardar na cache
    new_cache = RecipeCache(
        ingredients_hash=ingredients_hash,
        response_json=json.dumps(parsed)
    )
    db.add(new_cache)
    db.commit()

    return parsed


# ========================
# AUTH ROUTES
# ========================

@app.post("/register")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    email = data.email
    password = data.password

    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Email já existe")

    user = User(email=email, password=hash_password(password))
    db.add(user)
    db.commit()

    return {"message": "criado"}


@app.post("/login")
def login(email: str, password: str, db: Session = Depends(get_db)):

    user = db.query(User).filter(User.email == email).first()

    if not user or not verify_password(password, user.password):
        raise HTTPException(401, "Credenciais inválidas")

    token = create_token({"id": user.id})
    return {"access_token": token}


@app.post("/update-preferences")
def update_preferences(
    gluten_free: bool,
    vegetarian: bool,
    vegan: bool,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    current_user.dietary_gluten_free = gluten_free
    current_user.dietary_vegetarian = vegetarian
    current_user.dietary_vegan = vegan
    db.commit()

    return {"message": "Preferências atualizadas"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ========================
# STRIPE CHECKOUT
# ========================

@app.post("/create-checkout-session")
def create_checkout_session(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    try:
        # Criar customer se não existir
        if not current_user.stripe_customer_id:
            customer = stripe.Customer.create(email=current_user.email)
            current_user.stripe_customer_id = customer.id
            db.commit()

        session = stripe.checkout.Session.create(
            customer=current_user.stripe_customer_id,
            payment_method_types=["card"],
            mode="subscription",
            line_items=[
                {
                    "price": STRIPE_PRICE_ID,
                    "quantity": 1,
                }
            ],
            success_url="https://google.com",
            cancel_url="https://google.com",
        )

        return {"checkout_url": session.url}

    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.post("/create-portal-session")
def create_portal_session(
    current_user: User = Depends(get_current_user)
):
    if not current_user.stripe_customer_id:
        raise HTTPException(400, "Sem subscrição ativa")

    session = stripe.billing_portal.Session.create(
        customer=current_user.stripe_customer_id,
        return_url="https://teu-frontend.com"
    )

    return {"url": session.url}


# ========================
# STRIPE WEBHOOK
# ========================

@app.post("/stripe-webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except Exception:
        raise HTTPException(400, "Webhook inválido")

    event_type = event["type"]

    # PAGAMENTO CONCLUÍDO → vira premium

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        customer_id = session["customer"]

        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.plan = "premium"
            db.commit()

    # SUBSCRIÇÃO CANCELADA → volta a free

    if event_type == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription["customer"]

        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.plan = "free"
            db.commit()

    #  FALHA DE PAGAMENTO → volta a free
    
    if event_type == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_id = invoice["customer"]

        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.plan = "free"
            db.commit()

    return {"status": "success"}

# ========================
# IMAGE ANALYSIS
# ========================

@app.post("/analyze-image/")
@limiter.limit("5/minute")
async def analyze_image(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Detectar idioma automaticamente
    accept_language = request.headers.get("accept-language")

    if accept_language:
        language = accept_language.split(",")[0]
    else:
        language = "en-US"

    hoje = date.today()

    if current_user.last_analysis_date != hoje:
        current_user.analyses_today = 0
        current_user.last_analysis_date = hoje

    limite = 1 if current_user.plan == "free" else 3

    if current_user.analyses_today >= limite:
        raise HTTPException(403, "Limite diário atingido")

    image_bytes = await file.read()

    if len(image_bytes) > 5_000_000:
        raise HTTPException(400, "Imagem demasiado grande")

    try:
        Image.open(io.BytesIO(image_bytes)).verify()
    except:
        raise HTTPException(400, "Imagem inválida")

    ingredients = detect_ingredients(image_bytes)
    ingredient_names = [i["name"] for i in ingredients]

    recipes = generate_recipes(
        ingredient_names,
        current_user,
        db,
        language=language
    )

    current_user.analyses_today += 1
    db.commit()

    return {
        "ingredients_detected": ingredients,
        "recipes": recipes["recipes"]
    }
