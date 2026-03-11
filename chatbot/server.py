from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
from chatbot import setup_chatbot
from typing import List, Optional
import nest_asyncio
import uvicorn
import json
import db
import requests
import re
import urllib.parse
from config import ORDER_API_URL, X_PUBLISHABLE_KEY

# ---------------------------------------------------------------------------
# Color alias map: canonical color group -> all aliases (Portuguese + English)
# Used to match the user's requested color to the correct product variant.
# ---------------------------------------------------------------------------
COLOR_MAP = {
    "green":      ["green", "verde", "esmeralda", "verde oliva", "verde alecrim", "verde floresta", "verde musgo", "verde militar", "verde escuro", "verde pistache", "verde / menta", "leaves / esmeralda", "verde água", "verde áqua", "verdes"],
    "blue":       ["blue", "azul", "azul escuro", "azul claro", "azul céu", "azul índigo", "azul marinho", "azul mar", "azul / aqua", "aqua", "mandala / azul escuro", "atlântica / azul e petróleo", "blueberry / vanilla", "paisley / petróleo", "azul celeste", "celeste", "azul mar"],
    "black":      ["black", "preto", "preta", "preto e branco", "preto e verde", "bandhani / preto e branco", "amazônia / preto e verde", "grafite"],
    "white":      ["white", "branco", "branca", "offwhite", "off-white"],
    "red":        ["red", "vermelho", "vermelha", "vermelho / laranja", "amora", "amora / rosa", "lótus / amora", "mayuri / bordô", "bordô", "vinho"],
    "purple":     ["purple", "roxo", "roxa", "lilás", "lilás / azul", "ameixa", "ameixa e rosê", "pink / roxo", "cerrado / ameixa e rosê", "cinza / ameixa", "mandala roxo"],
    "pink":       ["pink", "rosa", "rosê", "rosa chá", "rosa goiaba", "rosa orquídea", "goiaba", "amora / rosa"],
    "beige":      ["beige", "bege", "bege escuro", "bege e azul", "madurai / bege", "raja / nude", "pantanal / bege e azul", "avelã", "nude", "bege / cinza"],
    "brown":      ["brown", "marrom", "café", "cacau", "cinza eucalipto", "telha", "terracota", "avelã"],
    "grey":       ["grey", "gray", "cinza", "cinza claro", "cinza nude", "grafite", "bege / cinza", "cinza eucalipto"],
    "gold":       ["gold", "dourado", "açafrão", "amarelo", "amarelo ocre", "ocre"],
    "turquoise":  ["turquoise", "turquesa", "petróleo", "oceano", "aqua", "azul / aqua", "mandala turquesa"],
    "orange":     ["orange", "laranja", "pêssego", "caatinga / pêssego e azul", "telha", "terracota"],
    "yellow":     ["yellow", "amarelo", "amarela", "amarelo ocre", "açafrão", "ocre"],
}

# Reverse map: lowercased alias -> color_group  (e.g. "preto" -> "black")
_ALIAS_TO_GROUP = {alias.lower(): group for group, aliases in COLOR_MAP.items() for alias in aliases}

# All aliases sorted by length DESC so longer phrases are matched before shorter ones
# (prevents "azul" from matching when user said "azul céu")
_SORTED_ALIASES = sorted(_ALIAS_TO_GROUP.keys(), key=len, reverse=True)

# ---------------------------------------------------------------------------

# Fix for "asyncio.run() cannot be called from a running event loop"
nest_asyncio.apply()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global chat_engine, product_lookup
    print("Building product lookup cache...")
    product_lookup = build_product_lookup()
    
    print("Initializing Database...")
    db.init_db()
    
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
            
            # Build per-variant thumbnail map: lowercased variant title -> thumbnail URL
            variant_images = {}
            for v in variants:
                v_title = v.get("title", "")
                pvi = v.get("product_variant_images")
                if pvi and isinstance(pvi, dict):
                    v_thumb = pvi.get("thumbnail")
                    if v_thumb:
                        variant_images[v_title.lower()] = {"image": v_thumb, "title": v_title}
            
            # Extract all options for dynamic URL parameters
            options_dict = {}
            for opt in p.get("options", []):
                o_title = opt.get("title")
                o_values = opt.get("values", [])
                if o_title and o_values:
                    # Capture the first value as the default
                    options_dict[o_title] = o_values[0].get("value")
            
            handle = p.get('handle', '')
            # Heuristic for category-based path prefix
            path_prefix = "produto"
            if "tapete" in handle.lower() or "tapete" in title.lower():
                path_prefix = "tapete-de-yoga"
            elif "almofada" in handle.lower() or "zafu" in handle.lower() or "almofada" in title.lower():
                path_prefix = "almofadas-de-yoga"
            elif "bolsa" in handle.lower() or "bolsa" in title.lower():
                path_prefix = "bolsas-para-yoga"

            lookup[title] = {
                "title": title,
                "handle": handle,
                "path_prefix": path_prefix,
                "price": price,
                "image": p.get("thumbnail") or (p.get("images")[0]["url"] if p.get("images") else "https://via.placeholder.com/200"),
                "url": f"https://test.yogateria.com.br/{path_prefix}/{handle}",
                "variant_images": variant_images,  # {variant_title_lower: {"image": thumbnail_url, "title": original_title}}
                "options": options_dict
            }
        return lookup
    except Exception as e:
        print(f"Lookup Error: {e}")
        return {}

# Startup logic handled by lifespan

class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = None

class FeedbackRequest(BaseModel):
    message_id: int
    feedback: str # "up" or "down"

@app.post("/feedback")
def submit_feedback(request: FeedbackRequest):
    """Submit feedback (thumbs up/down) for a chat message."""
    print(f"Received feedback '{request.feedback}' for message ID: {request.message_id}")
    
    if request.feedback not in ["up", "down"]:
        raise HTTPException(status_code=400, detail="Feedback must be 'up' or 'down'")
    
    # Update main table
    try:
        success = db.update_chat_feedback(request.message_id, request.feedback)
        if not success:
            print(f"Failed to update chat_history for ID {request.message_id}")
            raise HTTPException(status_code=500, detail="Failed to save feedback to history")

        # Save to specific tables as well
        if request.feedback == "up":
            print(f"Saving to GOOD_FEEDBACK table for ID {request.message_id}")
            db.save_good_feedback(request.message_id)
        elif request.feedback == "down":
            print(f"Saving to BAD_FEEDBACK table for ID {request.message_id}")
            db.save_bad_feedback(request.message_id)
            
        print("Feedback saved successfully.")
        return {"status": "success", "message": "Feedback received"}
    except Exception as e:
        print(f"Feedback Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    """Check server and database status."""
    db_status = "connected"
    db_rows = 0
    try:
        conn = db.get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM chat_history")
            db_rows = cur.fetchone()[0]
            cur.close()
            conn.close()
        else:
            db_status = "disconnected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "ok",
        "chatbot_ready": chat_engine is not None,
        "database": db_status,
        "total_chat_messages": db_rows
    }

@app.get("/history")
def get_chat_history(limit: int = 50):
    """Retrieve chat history from the database."""
    try:
        conn = db.get_db_connection()
        if not conn:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_message, bot_response, timestamp FROM chat_history ORDER BY timestamp DESC LIMIT %s",
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        history = [
            {
                "id": row[0],
                "user_message": row[1],
                "bot_response": row[2],
                "timestamp": row[3].isoformat() if row[3] else None
            }
            for row in rows
        ]
        return {"total": len(history), "history": history}
    except HTTPException:
        raise
    except Exception as e:
        print(f"History Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/history")
def clear_chat_history():
    """Clear all chat history from the database."""
    try:
        conn = db.get_db_connection()
        if not conn:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        cur = conn.cursor()
        cur.execute("DELETE FROM chat_history")
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return {"message": f"Cleared {deleted} chat messages from history"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Clear History Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/{user_id}")
def get_user_info(user_id: str):
    import os
    try:
        order_path = os.path.join(os.path.dirname(__file__), 'orders.json')
        if os.path.exists(order_path):
            with open(order_path, 'r', encoding='utf-8') as f:
                order_data = json.load(f)
            
            for order in order_data.get('orders', []):
                customer = order.get('customer', {})
                uid = str(customer.get('id', '')).lower()
                uemail = str(customer.get('email', '')).lower()
                query = str(user_id).lower()
                
                if uid == query or uemail == query:
                    first_name = customer.get('first_name')
                    last_name = customer.get('last_name')
                    email = customer.get('email', '')
                    
                    name = ""
                    if first_name and last_name:
                        name = f"{first_name} {last_name}"
                    elif first_name:
                        name = first_name
                    elif email:
                        name = email.split('@')[0]
                    else:
                        name = "User"
                        
                    return {"name": name, "email": email}
                    
        return {"name": user_id, "email": ""}
    except Exception as e:
        print(f"Error fetching user info: {e}")
        return {"name": user_id, "email": ""}

def fetch_order_info(query: str, user_id: str = None) -> str:
    if not ORDER_API_URL:
        return ""
        
    match = re.search(r'(order|pedido|cart|carrinho)\s*#?\s*([a-zA-Z0-9_-]+)', query, re.IGNORECASE)
    if not match:
        return ""
        
    req_type = match.group(1).lower()
    item_id = match.group(2)
    is_cart = req_type in ['cart', 'carrinho'] or item_id.startswith('cart_')
    
    headers = {}
    if X_PUBLISHABLE_KEY:
        headers['x-publishable-api-key'] = X_PUBLISHABLE_KEY
        
    try:
        if is_cart:
            cart_api_url = ORDER_API_URL.replace('/orders', '/carts')
            url = f"{cart_api_url}/{item_id}"
            resp = requests.get(url, headers=headers)
            
            if resp.status_code == 200:
                data = resp.json().get('cart', {})
                items = []
                for item in data.get('items', []):
                    qty = item.get('quantity', 1)
                    title = item.get('title', 'Item')
                    items.append(f"{qty}x {title}")
                    
                items_str = ", ".join(items) if items else "No items found"
                return f"System Note: The user (ID: {user_id}) is asking about cart #{item_id}. API Data: Items: {items_str}."
            else:
                return f"System Note: Could not fetch cart {item_id}. Status Code: {resp.status_code}"
        else:
            # Try getting by ID
            url = f"{ORDER_API_URL}/{item_id}"
            resp = requests.get(url, headers=headers)
            
            # If order not found by ID, try grabbing by display ID
            if resp.status_code == 404:
                url2 = f"{ORDER_API_URL}?display_id={item_id}"
                # Medusa often requires email along with display_id to fetch orders
                if user_id:
                    url2 += f"&email={user_id}"
                    
                resp2 = requests.get(url2, headers=headers)
                if resp2.status_code == 200:
                    orders = resp2.json().get('orders', [])
                    if orders:
                        data = orders[0]
                    else:
                        return f"System Note: No order found for display_id {item_id} and email {user_id}."
                else:
                    return f"System Note: Could not fetch order. Ensure the User ID (Email) matches the order email."
            elif resp.status_code == 200:
                data = resp.json().get('order', {})
            else:
                return ""
                
            status = data.get('status', 'unknown')
            fulfillment = data.get('fulfillment_status', 'unknown')
            
            items = []
            for item in data.get('items', []):
                qty = item.get('quantity', 1)
                title = item.get('title', 'Item')
                items.append(f"{qty}x {title}")
                
            items_str = ", ".join(items) if items else "No items found"
            return f"System Note: The user (ID: {user_id}) is asking about order #{item_id}. API Data: Status={status}, Fulfillment={fulfillment}. Items: {items_str}."
            
    except Exception as e:
        print(f"Error fetching API: {e}")
        return ""

def fetch_all_orders_for_user(user_id: str) -> str:
    if not user_id:
        return ""
    
    # Check local mock data in carts.json first
    import os
    info = ""
    try:
        carts_path = os.path.join(os.path.dirname(__file__), 'carts.json')
        if os.path.exists(carts_path):
            with open(carts_path, 'r', encoding='utf-8') as f:
                carts_data = json.load(f)
            
            for user in carts_data.get('users', []):
                uid = str(user.get('user_id', '')).lower()
                uemail = str(user.get('email', '')).lower()
                query = str(user_id).lower()
                if uid == query or uemail == query:
                    cart = user.get('cart', {})
                    info += f"System Note: The current user is {user.get('name')} (Email: {user.get('email')}, Phone: {user.get('phone')}).\n"
                    info += f"Delivery Address: {user.get('address')}.\n"
                    info += "They have the following recent tracked order items in their account:\n"
                    for item in cart.get('items', []):
                        info += f"- {item.get('quantity')}x {item.get('product_name')} (Variant: {item.get('variant')}) - Unit Price: R$ {item.get('unit_price')}\n"
                    info += f"Total: R$ {cart.get('cart_total')}. Free Shipping: {cart.get('free_shipping')}.\n"
                    return info
    except Exception as e:
        print(f"Error reading carts.json: {e}")

    try:
        order_path = os.path.join(os.path.dirname(__file__), 'orders.json')
        if os.path.exists(order_path):
            with open(order_path, 'r', encoding='utf-8') as f:
                order_data = json.load(f)
            
            user_orders = []
            for order in order_data.get('orders', []):
                uid = str(order.get('customer_id', '')).lower()
                customer = order.get('customer', {})
                uemail = str(customer.get('email', '')).lower()
                query = str(user_id).lower()
                if uid == query or uemail == query:
                    user_orders.append(order)
            
            if user_orders:
                info += f"\nSystem Note: The user also has {len(user_orders)} actual completed/past orders:\n"
                for o in user_orders[:10]: # limit to last 10
                    display_id = o.get('display_id', o.get('id', 'unknown'))
                    status = o.get('status', 'unknown')
                    fulfillment = o.get('fulfillment_status', 'unknown')
                    created_at = o.get('created_at', 'unknown').split('T')[0]
                    items = []
                    calc_total = 0
                    for item in o.get('items', []):
                        qty = item.get('quantity', 1)
                        title = item.get('product_title', item.get('title', 'Item'))
                        variant = item.get('variant_title', '')
                        u_price = item.get('unit_price', 0)
                        calc_total += (u_price * qty)
                        if variant and variant.lower() != 'default title':
                            items.append(f"{qty}x {title} ({variant}) - Unit Price: R$ {u_price}")
                        else:
                            items.append(f"{qty}x {title} - Unit Price: R$ {u_price}")
                            
                    items_str = ", ".join(items) if items else "No items found"
                    
                    summary_total = o.get('summary', {}).get('current_order_total', 0)
                    total = calc_total if calc_total > summary_total else summary_total
                    if calc_total > 0 and summary_total < 10: # Handle weird low totals in specific dumps
                         total = calc_total
                         
                    info += f"- Order #{display_id} (Date: {created_at}): Status={status}, Fulfillment={fulfillment}, Total: R$ {total}, Items: {items_str}.\n"
                return info # return here since we got local order data perfectly
            
            if info: # if we got cart info but no orders
                return info
    except Exception as e:
        print(f"Error reading order.json: {e}")

    # Fallback to API if ORDER_API_URL is configured
    if not ORDER_API_URL:
        return ""
        
    headers = {}
    if X_PUBLISHABLE_KEY:
        headers['x-publishable-api-key'] = X_PUBLISHABLE_KEY
        
    try:
        url = f"{ORDER_API_URL}?email={user_id}"
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            orders = resp.json().get('orders', [])
            if not orders:
                return f"System Note: The user {user_id} has no past orders."
                
            info += f"System Note: The user {user_id} has {len(orders)} orders available:\n"
            for data in orders[:5]: # limit to last 5 orders to save context
                status = data.get('status', 'unknown')
                display_id = data.get('display_id', data.get('id', 'unknown'))
                fulfillment = data.get('fulfillment_status', 'unknown')
                items = []
                for item in data.get('items', []):
                    qty = item.get('quantity', 1)
                    title = item.get('title', 'Item')
                    items.append(f"{qty}x {title}")
                items_str = ", ".join(items) if items else "No items found"
                info += f"- Order #{display_id}: Status={status}, Fulfillment={fulfillment}, Items: {items_str}.\n"
            return info
    except Exception as e:
        print(f"Error fetching orders for user: {e}")
        
    return ""

@app.post("/chat")
def chat_endpoint(request: ChatRequest):
    global chat_engine, product_lookup
    if not chat_engine:
        raise HTTPException(status_code=500, detail="Chatbot engine not initialized")
    
    try:
        user_message = request.message
        user_id = request.user_id
        
        # If the user explicitly puts a user ID in the chat, override the stored one
        id_match = re.search(r'cus_[a-zA-Z0-9]+', user_message)
        if id_match:
            user_id = id_match.group(0)
            
        # Also check for emails inside the chat message
        email_match = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', user_message)
        if email_match:
            user_id = email_match.group(0)
        
        # Always fetch user order context if user_id is provided
        system_context = ""
        if user_id:
            user_orders_info = fetch_all_orders_for_user(user_id)
            if user_orders_info:
                system_context = f"{user_orders_info}\n\n"
            else:
                system_context = f"System Note: The current user is {user_id}.\n\n"
                
        # Also check if user is asking for a specific order not in the list
        specific_order_info = fetch_order_info(user_message, user_id=user_id)
        if specific_order_info:
            system_context += f"{specific_order_info}\n\n"

        # --- Personalization Check for Clothing/Human Products ---
        is_clothing_query = bool(re.search(r'(dress|clothing|shirt|pants|legging|bra|top|shoe|wear|apparel|clothes|jacket|top|bottom)', user_message.lower()))
        is_providing_profile = bool(re.search(r'\b(male|female|man|woman|boy|girl|mens|womens)\b', user_message.lower()) or re.search(r'(size|\bxs\b|\bs\b|\bm\b|\bl\b|\bxl\b|\bxxl\b|\bxxxl\b|large|medium|small)', user_message.lower()))
        
        user_profile = None
        if user_id:
            user_profile = db.get_user_profile(user_id)
            
            if not user_profile:
                if is_clothing_query and not is_providing_profile:
                    resp_text = "To recommend the best yoga products for you, I need a bit of info first. Could you please tell me:\n\n- Your gender\n- Your usual clothing size for tops and/or bottoms For example: \"I am male, usually size L for tops and 42 for shoes.\""
                    message_id = db.save_chat_message(request.message, resp_text)
                    return {
                        "response": resp_text,
                        "products": [],
                        "message_id": message_id,
                        "follow_ups": []
                    }
                elif is_providing_profile:
                    # Try to extract gender explicitly for better context
                    gender = "Unknown"
                    msg_lower = user_message.lower()
                    if re.search(r'\b(male|man|boy|mens)\b', msg_lower):
                        gender = "Male"
                    elif re.search(r'\b(female|woman|girl|womens)\b', msg_lower):
                        gender = "Female"
                        
                    db.save_user_profile(user_id, gender, user_message)
                    user_profile = {"gender": gender, "size": user_message}
            
            if user_profile:
                g = user_profile['gender'].lower()
                sys_msg = f"System Note: The current user's profile with gender and size details is: Gender - {user_profile['gender']}, Details - '{user_profile['size']}'. "
                sys_msg += "CRITICAL: You MUST use this information to filter clothing products. "
                if g == 'male':
                    sys_msg += "The user is MALE. ONLY suggest men's upper and lower clothing. For shirts/camisetas, ONLY suggest the 'T-Shirt' variants. Do NOT suggest 'Baby Look' variants. Do NOT suggest sports bras, women's leggings, or female tops. "
                elif g == 'female':
                    sys_msg += "The user is FEMALE. ONLY suggest female clothing. For shirts/camisetas, ONLY suggest the 'Baby Look' variants. Do NOT suggest the 'T-Shirt' variants (which are for men). Suggest women's leggings, tops, and sports bras. "
                else:
                    sys_msg += "Filter the catalog explicitly by this gender and size. "
                system_context += sys_msg + "\n\n"
        # ---------------------------------------------------------
            
        # Determine if the query is order or cart related
        is_order_related = bool(re.search(r'(order|pedido|cart|carrinho|history|histórico|status|track|rastrear)', user_message, re.IGNORECASE))

        if system_context:
            if is_order_related:
                system_msg = f"User Account Data:\n{system_context}\nPlease use the above user and order information to answer the user's query.\n\nUser Query: {user_message}"
            else:
                system_msg = f"User Profile Context:\n{system_context}\n\nUser Query: {user_message}"
            response = chat_engine.chat(system_msg)
        else:
            response = chat_engine.chat(user_message)
            
        resp_text = str(response)
        
        # Parse Follow-ups
        follow_ups = []
        if "### FOLLOW-UPS:" in resp_text:
            parts = resp_text.split("### FOLLOW-UPS:")
            resp_text = parts[0].strip()
            follow_ups_raw = parts[-1].strip().split("\n")
            for line in follow_ups_raw:
                line = line.strip()
                if line.startswith("- "):
                    follow_ups.append(line[2:].strip())
                elif line.startswith("* "):
                    follow_ups.append(line[2:].strip())
        
        # Save to DB
        message_id = db.save_chat_message(request.message, resp_text)
        
        # Extract product cards using the lookup cache
        products = []
        
        # Check if the query is a basic greeting or non-product query
        is_basic_greeting = bool(re.search(r'^(hi|hello|hey|ola|olá|oi|bom dia|boa tarde|boa noite|thanks|thank you|obrigado|obrigada|tks|how are you|tudo bem|who are you|quem é você|help|ajuda).*$', user_message.strip(), re.IGNORECASE))
        
        if not is_order_related and not is_basic_greeting:
            seen_titles = set()
            
            # Combined text to scan for variant keywords (user query + bot response)
            scan_text = (user_message + " " + resp_text).lower()

            def detect_requested_color_group():
                """Return the color GROUP name the user is asking for, or None.
                Priority: user message first, then combined scan_text.
                """
                user_msg_lower = user_message.lower()
                # First pass: check ONLY the user message (more precise)
                for alias in _SORTED_ALIASES:
                    if re.search(r'\b' + re.escape(alias) + r'\b', user_msg_lower):
                        return _ALIAS_TO_GROUP[alias]
                # Second pass: wordless aliases (short single-word) in full scan_text
                for alias in _SORTED_ALIASES:
                    if alias in scan_text:
                        return _ALIAS_TO_GROUP[alias]
                return None

            # Calculate once for all cards
            requested_color_group = detect_requested_color_group()
            print(f"[Color Debug] requested_color_group='{requested_color_group}' from user_message: '{user_message[:120]}'")

            def resolve_variant_image(info):
                """
                Use COLOR_MAP to detect what color the user wants, then find the variant
                of this product that belongs to that color group.
                Returns a tuple (image_url, matched_color_group, matched_original_variant_title).
                Falls back to the general product thumbnail if no color match is found.
                """
                variant_images = info.get("variant_images", {}) # {lower: {"image": thumbnail_url, "title": original_title}}
                if not variant_images:
                    return info["image"], None, None

                if requested_color_group:
                    group_aliases = [a.lower() for a in COLOR_MAP[requested_color_group]]

                    # Step 1: Exact match — variant title IS one of the group aliases
                    for v_title_lower, v_data in variant_images.items():
                        if v_title_lower in group_aliases:
                            v_thumb = v_data["image"]
                            v_title_orig = v_data["title"]
                            print(f"[Color Debug]   exact match: '{v_title_lower}' → {v_thumb[:60]}")
                            return v_thumb, requested_color_group, v_title_orig

                    # Step 2: Substring match — any alias keyword appears in the variant title
                    for v_title_lower, v_data in variant_images.items():
                        for alias in group_aliases:
                            if alias in v_title_lower:
                                v_thumb = v_data["image"]
                                v_title_orig = v_data["title"]
                                print(f"[Color Debug]   substring match: alias='{alias}' in variant='{v_title_lower}'")
                                return v_thumb, requested_color_group, v_title_orig

                    # Step 3: Reverse substring — variant title is contained in an alias
                    for v_title_lower, v_data in variant_images.items():
                        for alias in group_aliases:
                            if v_title_lower in alias:
                                v_thumb = v_data["image"]
                                v_title_orig = v_data["title"]
                                print(f"[Color Debug]   reverse match: variant='{v_title_lower}' in alias='{alias}'")
                                return v_thumb, requested_color_group, v_title_orig

                    print(f"[Color Debug]   no variant match for group '{requested_color_group}'. Available: {list(variant_images.keys())}")
                    # Product doesn't have this color — still return default image but note no color match
                    return info["image"], None, None

                # No color requested → fall back to general product thumbnail
                return info["image"], None, None

            def construct_dynamic_url(info, matched_variant_title):
                """
                Constructs the dynamic URL with query parameters from product options.
                Matched variant title (color) overrides the default color parameter.
                """
                handle = info.get("handle", "")
                path_prefix = info.get("path_prefix", "produto")
                options = info.get("options", {}).copy()
                
                # If we have a detected color variant, update the URL parameters
                if matched_variant_title:
                    # User expects 'Cor' as the parameter key in the example
                    # If the tech option is 'Design' or 'Cor', we use 'Cor' as the canonical URL parameter
                    if "Cor" in options:
                        options["Cor"] = matched_variant_title
                    elif "Design" in options:
                        options["Cor"] = matched_variant_title
                        del options["Design"] # map Design -> Cor for the URL
                    else:
                        options["Cor"] = matched_variant_title
                
                # Encode all options as query parameters
                params = urllib.parse.urlencode(options)
                return f"https://test.yogateria.com.br/{path_prefix}/{handle}?{params}"

            def get_meaningful_words(text):
                words = set(re.findall(r'\b\w+\b', text.lower()))
                stops = {'de', 'para', 'e', 'o', 'a', 'com', 'da', 'do', 'em', 'um', 'uma', 'é', 'os', 'as', 'no', 'na', 'por', 'que', 'se'}
                return words - stops

            resp_lower = resp_text.lower()
            resp_words = get_meaningful_words(resp_text)

            # 1. Prioritize products whose exact full titles are in the response
            for title, info in product_lookup.items():
                if title in seen_titles:
                    continue
                if len(title) > 4 and title.lower() in resp_lower:
                    card = dict(info)
                    img, matched_color, matched_variant_title = resolve_variant_image(info)
                    card["image"] = img
                    card["color"] = matched_color  # e.g. "black", "green", or None
                    card["variant"] = matched_variant_title  # original case title
                    card["available_colors"] = list(info.get("variant_images", {}).keys())
                    card["url"] = construct_dynamic_url(info, matched_variant_title)
                    
                    # Skip this card if color was requested but product has no matching variant
                    if requested_color_group and not matched_color:
                        print(f"[Card] Skipping '{title}' — no {requested_color_group} variant available")
                        continue
                    products.append(card)
                    seen_titles.add(title)
                    print(f"[Card] Exact title match: '{title}' color='{matched_color}' variant='{matched_variant_title}'")
                if len(products) >= 3:
                    break

            # 2. Fuzzy match: score product titles by word overlap with the response
            if len(products) < 3:
                scored = []
                for title, info in product_lookup.items():
                    if title in seen_titles:
                        continue
                    # Use base title (strip variant suffixes after ' - ' or ' / ')
                    normalized = title.replace('\u2013', '-').replace('\u2014', '-')
                    base = normalized.split(' - ')[0].split(' / ')[0].strip()
                    title_words = get_meaningful_words(base)
                    if not title_words:
                        continue
                    overlap = title_words.intersection(resp_words)
                    ratio = len(overlap) / len(title_words)
                    # Accept if ≥3 words match OR ≥75% of title words match
                    if len(overlap) >= 3 or ratio >= 0.75:
                        scored.append((ratio, title, info))

                # Sort by match ratio desc, then add best matches
                scored.sort(key=lambda x: -x[0])
                for ratio, title, info in scored:
                    if title in seen_titles:
                        continue
                    card = dict(info)
                    img, matched_color, matched_variant_title = resolve_variant_image(info)
                    card["image"] = img
                    card["color"] = matched_color
                    card["variant"] = matched_variant_title
                    card["available_colors"] = list(info.get("variant_images", {}).keys())
                    card["url"] = construct_dynamic_url(info, matched_variant_title)
                    
                    # Skip if color was requested but product doesn't have that color
                    if requested_color_group and not matched_color:
                        print(f"[Card] Fuzzy skip '{title}' — no {requested_color_group} variant")
                        continue
                    products.append(card)
                    seen_titles.add(title)
                    print(f"[Card] Fuzzy title match ({ratio:.0%}): '{title}' color='{matched_color}'")
                    if len(products) >= 3:
                        break

            # 3. Check source nodes as last resort
            if len(products) < 3 and hasattr(response, 'source_nodes'):
                for node in response.source_nodes:
                    metadata = node.node.metadata
                    title = metadata.get('title')

                    if title and title in product_lookup and title not in seen_titles:
                        normalized_title = title.replace('\u2013', '-').replace('\u2014', '-')
                        main_part = normalized_title.split('-')[0].split('/')[0].strip()

                        title_words = get_meaningful_words(main_part)
                        overlap = title_words.intersection(resp_words)

                        if len(overlap) >= 3 or (len(title_words) > 0 and len(overlap) / len(title_words) >= 0.75):
                            info = product_lookup[title]
                            card = dict(info)
                            img, matched_color, matched_variant_title = resolve_variant_image(info)
                            card["image"] = img
                            card["color"] = matched_color
                            card["variant"] = matched_variant_title
                            card["available_colors"] = list(info.get("variant_images", {}).keys())
                            card["url"] = construct_dynamic_url(info, matched_variant_title)
                            
                            # Skip if color was requested but product doesn't have that color
                            if requested_color_group and not matched_color:
                                print(f"[Card] Source skip '{title}' — no {requested_color_group} variant")
                                continue
                            products.append(card)
                            seen_titles.add(title)
                            print(f"[Card] Source node match: '{title}' color='{matched_color}'")

                    if len(products) >= 3:
                        break

        return {
            "response": resp_text,
            "products": products,
            "message_id": message_id,
            "follow_ups": follow_ups
        }
    except Exception as e:
        print(f"Chat Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import subprocess as _sp, time as _t

    PORT = 8005

    # ── Auto-free port 8005 so re-running the server always works ─────────────
    try:
        result = _sp.run(
            ["lsof", "-ti", f":{PORT}"],
            capture_output=True, text=True
        )
        pids = [p.strip() for p in result.stdout.strip().split() if p.strip()]
        for pid in pids:
            _sp.run(["kill", "-9", pid], capture_output=True)  # SIGKILL — cannot be ignored
            print(f"[Server] Freed port {PORT} (killed PID {pid})")
        if pids:
            _t.sleep(1.5)  # give OS time to fully release the port
    except Exception as _e:
        print(f"[Server] Port-cleanup warning: {_e}")
    # ──────────────────────────────────────────────────────────────────────────

    uvicorn.run(app, host="0.0.0.0", port=PORT)