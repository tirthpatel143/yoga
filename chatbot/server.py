from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
from chatbot import setup_chatbot
import nest_asyncio
import uvicorn
import json

# Fix for "asyncio.run() cannot be called from a running event loop"
nest_asyncio.apply()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global chat_engine, product_lookup
    print("Building product lookup cache...")
    product_lookup = build_product_lookup()
    
    print("Initializating Chatbot Engine...")
    chat_engine = setup_chatbot()
    yield
    print("Application shutdown complete.")

app = FastAPI(title="Yogateria Chatbot API", lifespan=lifespan)

# Enable CORS for frontend interaction
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instances
chat_engine = None
product_lookup = {}

def build_product_lookup():
    """Build a cache of product details for the UI cards"""
    try:
        from config import PRODUCT_DATA_PATH
        with open(PRODUCT_DATA_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        lookup = {}
        for p in data.get("products", []):
            title = p.get("title")
            if not title: continue
            
            # Get first variant price
            price = "Available on site"
            variants = p.get("variants", [])
            if variants:
                calc = variants[0].get("calculated_price", {})
                if calc.get("calculated_amount"):
                    price = f"R$ {calc['calculated_amount']}"
            
            lookup[title] = {
                "title": title,
                "price": price,
                "image": p.get("thumbnail") or (p.get("images")[0]["url"] if p.get("images") else "https://via.placeholder.com/200"),
                "url": f"https://yogateria.com.br/produto/{p.get('handle', '')}"
            }
        return lookup
    except Exception as e:
        print(f"Lookup Error: {e}")
        return {}

# Startup logic handled by lifespan

class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
def chat_endpoint(request: ChatRequest):
    global chat_engine, product_lookup
    if not chat_engine:
        raise HTTPException(status_code=500, detail="Chatbot engine not initialized")
    
    try:
        response = chat_engine.chat(request.message)
        resp_text = str(response)
        
        # Extract product cards using the lookup cache
        products = []
        seen_titles = set()
        
        # 1. Prioritize products actually mentioned in the response text
        for title, info in product_lookup.items():
            # Check for title or significant part of title in response
            if title.lower() in resp_text.lower():
                products.append(info)
                seen_titles.add(title)
            if len(products) >= 3:
                break
        
        # 2. Add supplementary products from source nodes if needed
        if len(products) < 3 and hasattr(response, 'source_nodes'):
            for node in response.source_nodes:
                metadata = node.node.metadata
                title = metadata.get('title')
                
                # Check if we have detailed info for this product in our cache
                if title and title in product_lookup and title not in seen_titles:
                    products.append(product_lookup[title])
                    seen_titles.add(title)
                
                if len(products) >= 3: 
                    break

        return {
            "response": resp_text,
            "products": products
        }
    except Exception as e:
        print(f"Chat Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
