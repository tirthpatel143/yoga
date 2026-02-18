import qdrant_client
from llama_index.core import VectorStoreIndex, Settings, PromptTemplate
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.llms.huggingface_api import HuggingFaceInferenceAPI
from llama_index.embeddings.huggingface_api import HuggingFaceInferenceAPIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore

from config import (
    QDRANT_URL, COLLECTION_NAME, HF_TOKEN, 
    LLM_MODEL, EMBED_MODEL, TOP_K, SYSTEM_PROMPT, PRODUCT_DATA_PATH
)

def generate_price_summary():
    import json
    import os
    from config import PRODUCT_DATA_PATH
    
    if not os.path.exists(PRODUCT_DATA_PATH):
        return ""
        
    try:
        with open(PRODUCT_DATA_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        products = data.get("products", [])
        price_data = []
        for p in products:
            title = p.get("title", "")
            variants = p.get("variants", [])
            for v in variants:
                calc_price = v.get("calculated_price", {})
                if calc_price:
                    amount = calc_price.get("calculated_amount")
                    if amount is not None and amount > 0:
                        price_data.append((amount, title))
                        break # One price per product is enough for summary
        
        if not price_data:
            return ""
            
        price_data.sort()
        # Remove duplicates
        seen = set()
        unique = []
        for p, t in price_data:
            if t not in seen:
                unique.append((p, t))
                seen.add(t)
        
        cheapest = unique[:10]
        expensive = sorted(unique, key=lambda x: x[0], reverse=True)[:10]
        
        summary = "\n### GLOBAL PRICE SUMMARY (Direct from Catalog):\n"
        summary += "- Cheapest products: " + ", ".join([f"{t} (BRL {p})" for p, t in cheapest]) + "\n"
        summary += "- Most expensive products: " + ", ".join([f"{t} (BRL {p})" for p, t in expensive]) + "\n"
        return summary
    except Exception:
        return ""

def setup_chatbot():
    if not HF_TOKEN:
        print("ERROR: HF_TOKEN is not set.")
        return None

    # Configure models
    from llama_index.llms.openai_like import OpenAILike
    from config import OPENROUTER_API_KEY
    
    Settings.llm = OpenAILike(
        model=LLM_MODEL, 
        api_key=OPENROUTER_API_KEY,
        api_base="https://openrouter.ai/api/v1",
        is_chat_model=True
    )
    
    Settings.embed_model = HuggingFaceInferenceAPIEmbedding(
        model_name=EMBED_MODEL,
        token=HF_TOKEN
    )
    
    # Initialize Qdrant Client
    print(f"Connecting to Qdrant at {QDRANT_URL}...")
    client = qdrant_client.QdrantClient(url=QDRANT_URL)
    
    # Create Vector Store and get index
    vector_store = QdrantVectorStore(client=client, collection_name=COLLECTION_NAME)
    
    # Check if collection exists
    try:
        collections = client.get_collections().collections
        collection_names = [c.name for c in collections]
        
        if COLLECTION_NAME not in collection_names:
            print(f"WARNING: Collection '{COLLECTION_NAME}' not found.")
            # Fallback to base collection ONLY if it was created with LlamaIndex
            # Since we know 'yogateria_products' might be a raw collection, we should be careful
            if "yogateria_products" in collection_names:
                print("Checking 'yogateria_products' collection compatibility...")
                # Try to see if it has 'text' in payload schema - simplified check: just try to load
                vector_store = QdrantVectorStore(client=client, collection_name="yogateria_products")
            else:
                print("ERROR: No compatible collection found. Please run: python ingest.py")
                return None
    except Exception as e:
        print(f"Error connecting to Qdrant: {e}")
        return None

    try:
        index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
        
        # Setup Chat Engine with Dynamic Price Summary and Memory Buffer
        price_summary = generate_price_summary()
        # We need a cleaner system prompt for the chat engine
        # Chat engine handles context and query strings differently
        clean_system_prompt = SYSTEM_PROMPT.split("### CONTEXT:")[0].strip()
        enhanced_system_prompt = f"{clean_system_prompt}\n\n### GLOBAL CATALOG INFO:{price_summary}"
        
        # Initialize memory buffer to keep history efficient (Pro Level)
        memory = ChatMemoryBuffer.from_defaults(token_limit=1500)
        
        chat_engine = index.as_chat_engine(
            chat_mode="condense_plus_context",
            memory=memory,
            similarity_top_k=TOP_K,
            system_prompt=enhanced_system_prompt
        )
        return chat_engine
    except Exception as e:
        print(f"ERROR initializing index: {e}")
        print("Tip: This often happens if the collection schema is incompatible. Try running 'python ingest.py' for a fresh index.")
        return None

def chat():
    chat_engine = setup_chatbot()
    if not chat_engine:
        return

    print("\n" + "="*50)
    print("Welcome to Yogateria Support Chatbot!")
    print("I can help you with product information, features, and pricing.")
    print("Type 'exit' to quit.")
    print("="*50 + "\n")
    
    while True:
        try:
            user_input = input("You: ")
            if user_input.lower() in ['exit', 'quit', 'bye']:
                print("Chatbot: Namaste! Have a wonderful day!")
                break
            
            if not user_input.strip():
                continue
                
            print("Chatbot thinking...")
            response = chat_engine.chat(user_input)
            
            # Additional check for response validity
            if hasattr(response, 'response') and response.response:
                print(f"\nChatbot: {response.response}")
            elif str(response):
                print(f"\nChatbot: {response}")
            else:
                print("\nChatbot: I'm sorry, I couldn't find a definitive answer. Could you please rephrase?")
                
            print("-" * 30 + "\n")
            
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            if "TextNode" in str(e):
                print("\nData Error: The retrieval returned an invalid result. This usually means the Qdrant collection is corrupted or incompatible.")
                print("Please try running 'python ingest.py' to rebuild the index.")
            else:
                print(f"\nAn error occurred: {e}")

if __name__ == "__main__":
    chat()
