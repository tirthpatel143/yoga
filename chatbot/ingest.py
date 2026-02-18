import json
import os
import re
import qdrant_client
from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface_api import HuggingFaceInferenceAPIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore

from config import PRODUCT_DATA_PATH, QDRANT_URL, COLLECTION_NAME, HF_TOKEN, EMBED_MODEL, CHUNK_SIZE, CHUNK_OVERLAP

def clean_html(text):
    if not text:
        return ""
    # Remove HTML tags
    clean = re.sub('<[^<]+?>', '', text)
    # Decode some common entities
    clean = clean.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&quot;', '"').replace('&#39;', "'")
    # Remove multiple whitespace
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def load_products(file_path):
    print(f"Loading products from {file_path}...")
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return []
        
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    documents = []
    products = data.get("products", [])
    all_price_data = [] # For global summary
    
    for product in products:
        title = product.get("title", "")
        subtitle = product.get("subtitle", "") or ""
        description_raw = product.get("description", "") or ""
        clean_description = clean_html(description_raw)
        
        # Extract variants and pricing
        variants = product.get("variants", [])
        prices = []
        currencies = set()
        variant_details = []
        
        for variant in variants:
            calc_price = variant.get("calculated_price", {})
            if calc_price:
                amount = calc_price.get("calculated_amount")
                currency = calc_price.get("currency_code", "BRL").upper()
                if amount is not None:
                    prices.append(amount)
                    currencies.add(currency)
                    if amount > 0: # Exclude placeholders/free gifts for real spectrum analysis
                        all_price_data.append((amount, title))
            
            v_title = variant.get("title")
            if v_title:
                variant_details.append(v_title)
        
        price_text = ""
        if prices:
            min_p = min(prices)
            max_p = max(prices)
            currency_str = list(currencies)[0] if currencies else "BRL"
            if min_p == max_p:
                price_text = f"Price: {currency_str} {min_p}"
            else:
                price_text = f"Price Range: {currency_str} {min_p} - {max_p}"
        
        # Create a header for this product to be included in every chunk
        product_header = f"Product: {title}\n"
        if subtitle:
            product_header += f"Subtitle: {subtitle}\n"
        if price_text:
            product_header += f"{price_text}\n"
        if variant_details:
            product_header += f"Options: {', '.join(set(variant_details))}\n"
        
        full_text = product_header + "Description: " + clean_description
        
        metadata = {
            "product_id": product.get("id"),
            "handle": product.get("handle"),
            "title": title
        }
        
        doc = Document(text=full_text, metadata=metadata)
        documents.append(doc)
    
    # Generate Global Price Summary Document
    if all_price_data:
        all_price_data.sort()
        # Get unique product titles for extremes
        seen = set()
        unique_extremes = []
        for p, t in all_price_data:
            if t not in seen:
                unique_extremes.append((p, t))
                seen.add(t)
        
        cheapest = unique_extremes[:15]
        expensive = sorted(unique_extremes, key=lambda x: x[0], reverse=True)[:15]
        
        summary_text = "GLOBAL CATALOG PRICE SUMMARY & EXTREMES\n"
        summary_text += "Use this information for questions about cheapest, most expensive, or costly products.\n\n"
        
        summary_text += "### CHEAPEST PRODUCTS (Budget/Low Price):\n"
        for p, t in cheapest:
            summary_text += f"- {t}: BRL {p}\n"
            
        summary_text += "\n### MOST EXPENSIVE PRODUCTS (Premium/Costly):\n"
        for p, t in expensive:
            summary_text += f"- {t}: BRL {p}\n"
            
        summary_text += f"\nTotal products in catalog: {len(products)}\n"
        summary_text += "Keywords: cheapest product, cheapest item, lowest price, most expensive product, most costly, most premium, price range, affordable, budget."
        
        summary_doc = Document(
            text=summary_text, 
            metadata={"title": "Global Price Summary", "type": "catalog_metadata"}
        )
        documents.append(summary_doc)
        print("Generated and added Global Price Summary document to the index.")

    print(f"Processed {len(documents)} total documentation units (including summary).")
    return documents

def run_ingestion():
    if not HF_TOKEN:
        print("ERROR: HF_TOKEN is missing.")
        return

    # Settings
    Settings.embed_model = HuggingFaceInferenceAPIEmbedding(
        model_name=EMBED_MODEL,
        token=HF_TOKEN,
        embed_batch_size=10 # smaller batches to avoid timeouts
    )
    
    # Use SentenceSplitter for better chunking
    Settings.node_parser = SentenceSplitter(
        chunk_size=CHUNK_SIZE, 
        chunk_overlap=CHUNK_OVERLAP
    )

    # Load documents
    documents = load_products(PRODUCT_DATA_PATH)
    if not documents:
        return

    # Initialize Qdrant
    print(f"Connecting to Qdrant at {QDRANT_URL}...")
    client = qdrant_client.QdrantClient(url=QDRANT_URL)
    
    # Check if collection exists
    try:
        collections = client.get_collections().collections
        if any(c.name == COLLECTION_NAME for c in collections):
            print(f"Collection {COLLECTION_NAME} already exists. Deleting for fresh index...")
            client.delete_collection(COLLECTION_NAME)
    except Exception as e:
        print(f"Note: Could not check/delete collection: {e}")

    # Create Vector Store
    vector_store = QdrantVectorStore(client=client, collection_name=COLLECTION_NAME)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    # Build Index
    print("Building index... This might take a few minutes for 481 products.")
    try:
        index = VectorStoreIndex.from_documents(
            documents, 
            storage_context=storage_context,
            show_progress=True
        )
        print(f"Ingestion complete. Collection '{COLLECTION_NAME}' is ready.")
    except Exception as e:
        print(f"FAILED during index building: {e}")
        print("Tip: If it's a 500/503 error, just run ingest.py again. Hugging Face API can be unstable.")


if __name__ == "__main__":
    run_ingestion()
