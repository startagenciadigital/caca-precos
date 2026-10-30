#!/usr/bin/env python3
import re
import os
import sys
import subprocess
import json
import socket
import time
import threading
import unicodedata
from urllib.parse import quote_plus, unquote
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# Known store templates
KNOWN_STORES = {
    "Mercado Livre": {
        "url": "https://lista.mercadolivre.com.br/{query}",
        "currency": "BRL"
    },
    "Google Shopping": {
        "url": "https://www.google.com/search?tbm=shop&q={query}&hl=pt-BR",
        "currency": "BRL"
    },
    "Amazon Brasil": {
        "url": "https://www.amazon.com.br/s?k={query}",
        "currency": "BRL"
    },
    "PneuStore": {
        "url": "https://www.pneustore.com.br/busca/?q={query}",
        "currency": "BRL"
    },
    "eBay": {
        "url": "https://www.ebay.com/sch/i.html?_nkw={query}&_sop=15",
        "currency": "USD"
    },
    "Sweetwater": {
        "url": "https://www.sweetwater.com/store/search?s={query}",
        "currency": "USD"
    },
    "Guitar Center": {
        "url": "https://www.guitarcenter.com/search?Ntt={query}",
        "currency": "USD"
    },
    "B&H Photo": {
        "url": "https://www.bhphotovideo.com/c/search?Ntt={query}",
        "currency": "USD"
    },
    "Best Buy": {
        "url": "https://www.bestbuy.com/site/searchpage.jsp?st={query}",
        "currency": "USD"
    }
}

# Paths and Config
PORT = 18888
PRODUCTS_CONFIG_FILE = "/Users/macstudio-maj/.gemini/antigravity/scratch/yamaha-yc73-tracker/products.json"
DATA_JSON_FILE = "/Users/macstudio-maj/.gemini/antigravity/scratch/yamaha-yc73-tracker/data.json"
DASHBOARD_FILE = "/Users/macstudio-maj/.gemini/antigravity/scratch/yamaha-yc73-tracker/dashboard.html"
INDEX_FILE = "/Users/macstudio-maj/.gemini/antigravity/scratch/yamaha-yc73-tracker/index.html"

# State flags
is_searching = False
last_run_time = "N/A"

CONFIG_FILE = "/Users/macstudio-maj/.gemini/antigravity/scratch/yamaha-yc73-tracker/config.json"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "search_interval": "14:00_17:30"
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(default_config, f, indent=2)
        except Exception as e:
            print(f"Error creating default config: {e}")
            
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return {"search_interval": "14:00_17:30"}

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")

def get_last_search_timestamp():
    if os.path.exists(DATA_JSON_FILE):
        try:
            with open(DATA_JSON_FILE, 'r') as f:
                data = json.load(f)
                last_time_str = data.get("last_search_time", "N/A")
                if last_time_str != "N/A":
                    return datetime.strptime(last_time_str, "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception as e:
            print(f"Error reading last search time: {e}")
    return 0

def scheduler_loop():
    while True:
        try:
            config = load_config()
            interval = config.get("search_interval", "14:00_17:30")
            
            if interval != "manual":
                now = time.time()
                last_search = get_last_search_timestamp()
                
                should_run = False
                if interval == "1h" and (now - last_search >= 3600):
                    should_run = True
                elif interval == "3h" and (now - last_search >= 10800):
                    should_run = True
                elif interval == "6h" and (now - last_search >= 21600):
                    should_run = True
                elif interval == "12h" and (now - last_search >= 43200):
                    should_run = True
                elif interval == "14:00_17:30":
                    now_dt = datetime.now()
                    today_1400 = now_dt.replace(hour=14, minute=0, second=0, microsecond=0)
                    today_1730 = now_dt.replace(hour=17, minute=30, second=0, microsecond=0)
                    
                    last_search_dt = datetime.fromtimestamp(last_search)
                    
                    if now_dt >= today_1400 and last_search_dt < today_1400:
                        should_run = True
                    elif now_dt >= today_1730 and last_search_dt < today_1730:
                        should_run = True
                            
                if should_run:
                    print(f"[{datetime.now()}] Scheduler triggered search run for interval '{interval}'")
                    trigger_background_search()
        except Exception as e:
            print(f"Error in scheduler loop: {e}")
            
        time.sleep(30)

def update_product_target_price(product_id, target_price):
    # 1. Update products.json
    products = load_products()
    updated = False
    for prod in products:
        if prod.get("id") == product_id:
            prod["target_price"] = float(target_price)
            updated = True
            break
    if updated:
        try:
            with open(PRODUCTS_CONFIG_FILE, 'w') as f:
                json.dump(products, f, indent=2)
        except Exception as e:
            print(f"Error saving products config: {e}")
            return False
            
        # 2. Update data.json
        if os.path.exists(DATA_JSON_FILE):
            try:
                with open(DATA_JSON_FILE, 'r') as f:
                    data = json.load(f)
                if "products" in data and product_id in data["products"]:
                    data["products"][product_id]["target_price"] = float(target_price)
                    with open(DATA_JSON_FILE, 'w') as f:
                        json.dump(data, f, indent=2)
            except Exception as e:
                print(f"Error updating data.json target_price: {e}")
        
        # 3. Generate dashboard
        generate_dashboard()
        return True
    return False

def update_search_interval(interval):
    config = load_config()
    config["search_interval"] = interval
    save_config(config)
    
    # Update data.json with the interval
    if os.path.exists(DATA_JSON_FILE):
        try:
            with open(DATA_JSON_FILE, 'r') as f:
                data = json.load(f)
            data["search_interval"] = interval
            with open(DATA_JSON_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error updating data.json search_interval: {e}")
            
    # Regenerate dashboard to embed the updated config
    generate_dashboard()

def slugify(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = re.sub(r'[^\w\s-]', '', text).strip().lower()
    return re.sub(r'[-\s]+', '-', text)

def add_product(data):
    name = data.get("name", "").strip()
    if not name:
        return None, "Nome do produto é obrigatório."
    prod_id = data.get("id") or slugify(name)
    products = load_products()
    base_id = prod_id
    counter = 1
    existing_ids = {p["id"] for p in products}
    while prod_id in existing_ids:
        prod_id = f"{base_id}-{counter}"
        counter += 1
        
    currency = data.get("currency", "BRL").upper()
    try:
        target_price = float(data.get("target_price", 0.0))
    except (ValueError, TypeError):
        target_price = 0.0
    active = bool(data.get("active", True))
    
    stores = data.get("stores", {})
    if not stores:
        query_encoded = quote_plus(name)
        if currency == "BRL":
            stores = {
                "Mercado Livre": f"https://lista.mercadolivre.com.br/{query_encoded}",
                "Google Shopping": f"https://www.google.com/search?tbm=shop&q={query_encoded}&hl=pt-BR",
                "Amazon Brasil": f"https://www.amazon.com.br/s?k={query_encoded}"
            }
        else:
            stores = {
                "eBay": f"https://www.ebay.com/sch/i.html?_nkw={query_encoded}&_sop=15",
                "Sweetwater": f"https://www.sweetwater.com/store/search?s={query_encoded}"
            }
            
    relevancy = data.get("relevancy_keywords", [])
    if not relevancy:
        relevancy = [w.lower() for w in re.findall(r'\w+', name) if len(w) > 2]
        
    exclude = data.get("exclude_keywords", [])
    if not exclude and currency == "BRL":
        exclude = ["cabo", "fonte", "adaptador", "capa", "suporte", "manual", "adesivo", "case", "bag"]
        
    new_prod = {
        "id": prod_id,
        "name": name,
        "currency": currency,
        "target_price": target_price,
        "active": active,
        "stores": stores,
        "search_queries": {k: name for k in stores.keys()},
        "exclude_keywords": exclude,
        "relevancy_keywords": relevancy
    }
    
    products.append(new_prod)
    try:
        with open(PRODUCTS_CONFIG_FILE, 'w') as f:
            json.dump(products, f, indent=2)
    except Exception as e:
        return None, f"Erro ao salvar produto: {e}"
        
    # Update data.json
    if os.path.exists(DATA_JSON_FILE):
        try:
            with open(DATA_JSON_FILE, 'r') as f:
                d = json.load(f)
        except Exception:
            d = {"products": {}}
    else:
        d = {"products": {}}
        
    if "products" not in d:
        d["products"] = {}
    d["products"][prod_id] = {
        "name": name,
        "currency": currency,
        "active": active,
        "target_price": target_price,
        "best_deal": None,
        "deals": []
    }
    try:
        with open(DATA_JSON_FILE, 'w') as f:
            json.dump(d, f, indent=2)
    except Exception as e:
        print(f"Error updating data.json: {e}")
        
    generate_dashboard()
    return new_prod, None

def toggle_product_active(product_id, is_active=None):
    products = load_products()
    found = False
    new_state = False
    for p in products:
        if p.get("id") == product_id:
            if is_active is None:
                p["active"] = not p.get("active", True)
            else:
                p["active"] = bool(is_active)
            new_state = p["active"]
            found = True
            break
    if not found:
        return False, None
    try:
        with open(PRODUCTS_CONFIG_FILE, 'w') as f:
            json.dump(products, f, indent=2)
    except Exception as e:
        print(f"Error saving products.json: {e}")
        return False, None
        
    if os.path.exists(DATA_JSON_FILE):
        try:
            with open(DATA_JSON_FILE, 'r') as f:
                d = json.load(f)
            if "products" in d and product_id in d["products"]:
                d["products"][product_id]["active"] = new_state
                with open(DATA_JSON_FILE, 'w') as f:
                    json.dump(d, f, indent=2)
        except Exception as e:
            print(f"Error updating data.json: {e}")
            
    generate_dashboard()
    return True, new_state

def delete_product(product_id, delete_history=False):
    products = load_products()
    orig_len = len(products)
    products = [p for p in products if p.get("id") != product_id]
    if len(products) == orig_len:
        return False
        
    try:
        with open(PRODUCTS_CONFIG_FILE, 'w') as f:
            json.dump(products, f, indent=2)
    except Exception as e:
        print(f"Error saving products: {e}")
        return False
        
    if os.path.exists(DATA_JSON_FILE):
        try:
            with open(DATA_JSON_FILE, 'r') as f:
                d = json.load(f)
            if "products" in d and product_id in d["products"]:
                del d["products"][product_id]
                with open(DATA_JSON_FILE, 'w') as f:
                    json.dump(d, f, indent=2)
        except Exception as e:
            print(f"Error removing from data.json: {e}")
            
    if delete_history:
        log_file = get_log_file(product_id)
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
            except Exception as e:
                print(f"Error removing log file: {e}")
                
    generate_dashboard()
    return True

def load_products():
    if not os.path.exists(PRODUCTS_CONFIG_FILE):
        return []
    try:
        with open(PRODUCTS_CONFIG_FILE, 'r') as f:
            products = json.load(f)
            for p in products:
                if "currency" not in p:
                    p["currency"] = "USD"
                if "active" not in p:
                    p["active"] = True
            return products
    except Exception as e:
        print(f"Error loading products configuration: {e}")
        return []

def get_log_file(product_id):
    if product_id == "yamaha-yc73":
        return "/Users/macstudio-maj/.gemini/antigravity/scratch/yamaha-yc73-tracker/deals.md"
    else:
        return f"/Users/macstudio-maj/.gemini/antigravity/scratch/yamaha-yc73-tracker/deals_{product_id}.md"

def fetch_pages_dynamic(stores_dict):
    if not stores_dict:
        return {}
        
    print(f"[{datetime.now()}] Fetching {len(stores_dict)} stores via Safari AppleScript...")
    script_lines = [
        'tell application "Safari"',
        '    if not (exists window 1) then',
        '        make new document',
        '    end if'
    ]
    
    delimiters = []
    store_names = list(stores_dict.keys())
    
    for i, name in enumerate(store_names):
        raw_url = stores_dict[name]
        var_name = f"html_{i}"
        script_lines.append(f'    -- Fetch {name}')
        script_lines.append(f'    open location "{raw_url}"')
        script_lines.append('    delay 7')
        script_lines.append(f'    set {var_name} to source of front document')
        script_lines.append('    try')
        script_lines.append('        close current tab of front window')
        script_lines.append('    end try')
        delimiters.append(var_name)
        
    delimiter_expr = ' & "===DELIMITER===" & '.join(delimiters)
    script_lines.append(f'    return {delimiter_expr}')
    script_lines.append('end tell')
    
    applescript = "\n".join(script_lines)
    
    res = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"AppleScript Error: {res.stderr}")
        return {}
        
    parts = res.stdout.split("===DELIMITER===")
    result = {}
    for i, name in enumerate(store_names):
        if i < len(parts):
            result[name] = parts[i]
    return result

def parse_ebay(html, relevancy_keywords, exclude_keywords, rate):
    if not html:
        return []
    items = []
    
    blocks = html.split('class="su-card-container su-card-container--')
    if len(blocks) <= 1:
        blocks = html.split('su-card-container')
        
    for block in blocks[1:]:
        link_match = re.search(r'href=["\']?(https://www.ebay.com/itm/[^\s"\' >]+)', block)
        if not link_match:
            link_match = re.search(r'href=["\']?([^"\' >]+)["\']?[^>]*class=["\']?[^"\'>]*s-card__link', block)
            
        title_match = re.search(r's-card__title[^>]*>(?:<span[^>]*>)?\s*([^<]+)', block, re.DOTALL)
        if not title_match:
            title_match = re.search(r'alt=["\']?([^"\'<>]+)["\']?', block)
            
        price_match = re.search(r's-card__price[^>]*>\s*([^<]+)', block)
        
        if title_match and price_match:
            title = title_match.group(1).strip()
            price_raw = price_match.group(1).strip()
            link = link_match.group(1).split('?')[0] if link_match else "https://www.ebay.com"
            
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            
            title_lower = title_clean.lower()
            if not any(rk in title_lower for rk in relevancy_keywords):
                continue
            if any(keyword in title_lower for keyword in exclude_keywords):
                continue
                
            currency = "BRL" if "R$" in price_raw or "BRL" in price_raw else "USD"
            clean_num_str = re.sub(r'[^\d,\.]', '', price_raw)
            if re.search(r',\d{2}$', clean_num_str):
                clean_num_str = clean_num_str.replace('.', '').replace(',', '.')
            else:
                clean_num_str = clean_num_str.replace(',', '')
                
            try:
                price_val = float(clean_num_str)
                price_usd = price_val
                if currency == "BRL":
                    price_usd = price_val / rate
                    price_str = f"R$ {price_val:,.2f} (~${price_usd:,.2f})"
                else:
                    price_str = f"${price_val:,.2f}"
                
                items.append({
                    'title': title_clean,
                    'price': price_usd,
                    'price_str': price_str,
                    'link': link,
                    'source': 'eBay'
                })
            except Exception as e:
                print(f"Error parsing eBay price '{price_raw}': {e}")
    return items

def parse_bh(html, relevancy_keywords, exclude_keywords):
    if not html:
        return []
    items = []
    
    blocks = html.split('data-selenium="miniProductPageProductName"')
    for block in blocks[1:]:
        name_match = re.search(r'^[^>]*>([^<]+)</span>', block)
        if name_match:
            title_clean = name_match.group(1).strip()
            
            title_lower = title_clean.lower()
            if not any(rk in title_lower for rk in relevancy_keywords):
                continue
            if any(keyword in title_lower for keyword in exclude_keywords):
                continue
                
            url_match = re.search(r'href="([^"]+/product/[^"]+)"', block)
            if not url_match:
                url_match = re.search(r'href="([^"]+)"[^>]*data-selenium="miniProductPageSellingPointsDetailsLink"', block)
                
            price_first_match = re.search(r'data-selenium="uppedDecimalPriceFirst"[^>]*>\$([\d,]+)</span>', block)
            price_second_match = re.search(r'data-selenium="uppedDecimalPriceSecond"[^>]*>(\d+)</sup>', block)
            
            price_val = 0.0
            price_str = "N/A"
            if price_first_match and price_second_match:
                price_str = f"${price_first_match.group(1)}.{price_second_match.group(1)}"
                price_val = float(price_str.replace('$', '').replace(',', ''))
            
            url = "https://www.bhphotovideo.com" + url_match.group(1) if url_match else "https://www.bhphotovideo.com"
            
            if price_str != "N/A":
                items.append({
                    'title': title_clean,
                    'price': price_val,
                    'price_str': price_str,
                    'link': url,
                    'source': 'B&H Photo'
                })
    return items

def parse_bestbuy(html, relevancy_keywords, exclude_keywords):
    if not html:
        return []
    items = []
    
    blocks = html.split('class="product-image"')
    for block in blocks[1:]:
        title_match = re.search(r'<h3 class="product-title\s*" title="([^"]+)">', block)
        price_match = re.search(r'data-testid="price-block-customer-price"[^>]*>.*?<span[^>]*>\$([\d,]+\.\d{2})</span>', block, re.DOTALL)
        link_match = re.search(r'href="(https://www.bestbuy.com/product/[^"]+)"', block)
        
        if title_match:
            title_clean = title_match.group(1).strip()
            
            title_lower = title_clean.lower()
            if not any(rk in title_lower for rk in relevancy_keywords):
                continue
            if any(keyword in title_lower for keyword in exclude_keywords):
                continue
                
            price_str = price_match.group(1).strip() if price_match else "N/A"
            link = link_match.group(1) if link_match else "https://www.bestbuy.com"
            
            if price_str != "N/A":
                try:
                    price_val = float(price_str.replace(',', ''))
                    items.append({
                        'title': title_clean,
                        'price': price_val,
                        'price_str': f"${price_val:,.2f}",
                        'link': link,
                        'source': 'Best Buy'
                    })
                except Exception as e:
                    print(f"Error parsing Best Buy price '{price_str}': {e}")
    return items

def parse_sweetwater(html, relevancy_keywords, exclude_keywords):
    if not html:
        return []
    items = []
    
    start_pos = html.find('"resultsState"')
    if start_pos == -1:
        return []
        
    start_brace = html.find('{', start_pos)
    if start_brace == -1:
        return []
        
    bracket_count = 1
    idx = start_brace + 1
    while bracket_count > 0 and idx < len(html):
        char = html[idx]
        if char == '{':
            bracket_count += 1
        elif char == '}':
            bracket_count -= 1
        idx += 1
        
    full_json = html[start_brace:idx]
    try:
        data = json.loads(full_json)
        hits = data.get('results', [{}])[0].get('hits', [])
        for h in hits:
            brand = h.get('brand', '')
            prod_name = h.get('productName', '')
            title_clean = f"{brand} {prod_name}".strip()
            
            title_lower = title_clean.lower()
            if not any(rk in title_lower for rk in relevancy_keywords):
                continue
            if any(keyword in title_lower for keyword in exclude_keywords):
                continue
                
            price_dict = h.get('price', {}) or {}
            price_val = price_dict.get('finalPrice', 0.0)
            url_path = h.get('url', '')
            url = "https://www.sweetwater.com" + url_path if url_path else "https://www.sweetwater.com"
            
            if price_val > 0.0:
                items.append({
                    'title': title_clean,
                    'price': price_val,
                    'price_str': f"${price_val:,.2f}",
                    'link': url,
                    'source': 'Sweetwater'
                })
    except Exception as e:
        print(f"Error parsing Sweetwater JSON: {e}")
        
    return items

def parse_guitarcenter(html, relevancy_keywords, exclude_keywords):
    if not html:
        return []
    items = []
    
    blocks = html.split('class="plp-product-grid')
    if len(blocks) <= 1:
        blocks = html.split('plp-product-grid')
        
    for block in blocks[1:]:
        link_match = re.search(r'href="([^"]+)"', block)
        title_match = re.search(r'<h2[^>]*>([^<]+)</h2>', block)
        if not title_match:
            title_match = re.search(r'alt="([^"]+)"', block)
        price_match = re.search(r'class="[^"]*sale-price[^"]*"[^>]*>\$([\d,]+\.\d{2})</span>', block)
        
        if title_match and price_match:
            title_clean = title_match.group(1).strip()
            
            title_lower = title_clean.lower()
            if not any(rk in title_lower for rk in relevancy_keywords):
                continue
            if any(keyword in title_lower for keyword in exclude_keywords):
                continue
                
            price_str = price_match.group(1).strip()
            price_val = float(price_str.replace(',', ''))
            
            url = "https://www.guitarcenter.com" + link_match.group(1) if link_match else "https://www.guitarcenter.com"
            
            items.append({
                'title': title_clean,
                'price': price_val,
                'price_str': f"${price_val:,.2f}",
                'link': url,
                'source': 'Guitar Center'
            })
    return items

def parse_mercadolivre(html, relevancy_keywords, exclude_keywords):
    if not html:
        return []
    items = []
    blocks = re.split(r'class="[^"]*(?:ui-search-layout__item|poly-card|ui-search-result__wrapper)[^"]*"', html)
    if len(blocks) <= 1:
        blocks = html.split('ui-search-result')
    for block in blocks[1:]:
        link_match = re.search(r'href=["\']?(https://[^"\' >]*(?:mercadolivre\.com\.br|produto\.mercadolivre\.com\.br)[^"\' >]*)', block)
        title_match = re.search(r'class="[^"]*(?:ui-search-item__title|poly-component__title)[^"]*"[^>]*>(?:<a[^>]*>)?\s*([^<]+)', block, re.DOTALL)
        if not title_match:
            title_match = re.search(r'<h2[^>]*>\s*([^<]+)</h2>', block)
        price_int_match = re.search(r'class="andes-money-amount__fraction"[^>]*>([\d\.]+)', block)
        price_cents_match = re.search(r'class="andes-money-amount__cents"[^>]*>(\d+)', block)
        
        if title_match and (price_int_match or re.search(r'R\$\s*([\d\.]+(?:,\d{2})?)', block)):
            title = title_match.group(1).strip()
            title_lower = title.lower()
            if relevancy_keywords and not any(rk.lower() in title_lower for rk in relevancy_keywords):
                continue
            if exclude_keywords and any(kw.lower() in title_lower for kw in exclude_keywords):
                continue
                
            if price_int_match:
                cents = price_cents_match.group(1) if price_cents_match else "00"
                raw_num = price_int_match.group(1).replace('.', '') + "." + cents
                try:
                    price_val = float(raw_num)
                except Exception:
                    continue
            else:
                p_raw = re.search(r'R\$\s*([\d\.]+(?:,\d{2})?)', block).group(1)
                try:
                    price_val = float(p_raw.replace('.', '').replace(',', '.'))
                except Exception:
                    continue
                
            link = link_match.group(1).split('?')[0] if link_match else "https://www.mercadolivre.com.br"
            items.append({
                'title': title,
                'price': price_val,
                'price_str': f"R$ {price_val:,.2f}",
                'link': link,
                'source': 'Mercado Livre'
            })
    return items

def parse_google_shopping(html, relevancy_keywords, exclude_keywords):
    if not html:
        return []
    items = []
    blocks = re.split(r'class="[^"]*(?:sh-dgr__grid-result|sh-np__click-target|KZmu8e|sh-dgr__content)[^"]*"', html)
    if len(blocks) <= 1:
        blocks = re.split(r'<div[^>]+data-docid=', html)
    for block in blocks[1:]:
        title_match = re.search(r'class="[^"]*(?:tAxDx|EI11Pd)[^"]*"[^>]*>([^<]+)', block)
        if not title_match:
            title_match = re.search(r'aria-label="([^"]+)"', block)
        price_match = re.search(r'class="[^"]*a8Pemb[^"]*"[^>]*>([^<]+)', block)
        if not price_match:
            price_match = re.search(r'R\$\s*([\d\.]+(?:,\d{2})?)', block)
            
        merchant_match = re.search(r'class="[^"]*(?:aULzUe|IuHnof)[^"]*"[^>]*>([^<]+)', block)
        merchant = merchant_match.group(1).strip() if merchant_match else "Google Shopping"
        link_match = re.search(r'href="(/url\?q=[^"&]+|https://[^"]+)"', block)
        
        if title_match and price_match:
            title = title_match.group(1).strip()
            title_lower = title.lower()
            if relevancy_keywords and not any(rk.lower() in title_lower for rk in relevancy_keywords):
                continue
            if exclude_keywords and any(kw.lower() in title_lower for kw in exclude_keywords):
                continue
                
            p_str = price_match.group(1).strip() if hasattr(price_match, 'group') else str(price_match)
            clean_num = re.sub(r'[^\d,\.]', '', p_str)
            if re.search(r',\d{2}$', clean_num):
                clean_num = clean_num.replace('.', '').replace(',', '.')
            else:
                clean_num = clean_num.replace(',', '')
            try:
                price_val = float(clean_num)
            except Exception:
                continue
                
            link = link_match.group(1) if link_match else "https://www.google.com/shopping"
            if link.startswith('/url?q='):
                link = unquote(link[7:])
                
            items.append({
                'title': title,
                'price': price_val,
                'price_str': f"R$ {price_val:,.2f}",
                'link': link,
                'source': f"Google Shopping ({merchant})"
            })
    return items

def parse_amazon_br(html, relevancy_keywords, exclude_keywords):
    if not html:
        return []
    items = []
    blocks = html.split('data-component-type="s-search-result"')
    for block in blocks[1:]:
        title_match = re.search(r'<h2[^>]*>.*?<span[^>]*>([^<]+)</span>', block, re.DOTALL)
        price_whole = re.search(r'class="a-price-whole">([^<]+)</span>', block)
        price_frac = re.search(r'class="a-price-fraction">(\d+)</span>', block)
        link_match = re.search(r'href="(/[^"]*/dp/[^"/?]+|https://www\.amazon\.com\.br/[^"]*)"', block)
        
        if title_match and price_whole:
            title = title_match.group(1).strip()
            title_lower = title.lower()
            if relevancy_keywords and not any(rk.lower() in title_lower for rk in relevancy_keywords):
                continue
            if exclude_keywords and any(kw.lower() in title_lower for kw in exclude_keywords):
                continue
                
            frac = price_frac.group(1) if price_frac else "00"
            whole = price_whole.group(1).replace('.', '').replace(',', '').strip()
            try:
                price_val = float(f"{whole}.{frac}")
            except Exception:
                continue
                
            url = link_match.group(1) if link_match else "https://www.amazon.com.br"
            if url.startswith('/'):
                url = "https://www.amazon.com.br" + url
                
            items.append({
                'title': title,
                'price': price_val,
                'price_str': f"R$ {price_val:,.2f}",
                'link': url,
                'source': 'Amazon Brasil'
            })
    return items

def parse_pneustore(html, relevancy_keywords, exclude_keywords):
    if not html:
        return []
    items = []
    blocks = re.split(r'class="[^"]*(?:product-item|showcase-item|card-product)[^"]*"', html)
    for block in blocks[1:]:
        title_match = re.search(r'class="[^"]*product-name[^"]*"[^>]*>([^<]+)<', block)
        if not title_match:
            title_match = re.search(r'title="([^"]+)"', block)
        price_match = re.search(r'R\$\s*([\d\.]+(?:,\d{2})?)', block)
        link_match = re.search(r'href="(https://[^"]*pneustore\.com\.br/[^"]+)"', block)
        
        if title_match and price_match:
            title = title_match.group(1).strip()
            title_lower = title.lower()
            if relevancy_keywords and not any(rk.lower() in title_lower for rk in relevancy_keywords):
                continue
            if exclude_keywords and any(kw.lower() in title_lower for kw in exclude_keywords):
                continue
            try:
                price_val = float(price_match.group(1).replace('.', '').replace(',', '.'))
                link = link_match.group(1) if link_match else "https://www.pneustore.com.br"
                items.append({
                    'title': title,
                    'price': price_val,
                    'price_str': f"R$ {price_val:,.2f}",
                    'link': link,
                    'source': 'PneuStore'
                })
            except Exception:
                continue
    return items

def parse_generic_store(html, store_name, relevancy_keywords, exclude_keywords, currency="BRL"):
    if not html:
        return []
    items = []
    link_blocks = re.findall(r'<a[^>]+href=["\'](https?://[^"\']+)["\'][^>]*>(.*?)</a>', html, re.DOTALL | re.IGNORECASE)
    for link, content in link_blocks[:50]:
        text = re.sub(r'<[^>]+>', ' ', content).strip()
        if len(text) < 10:
            continue
        text_lower = text.lower()
        if relevancy_keywords and not any(rk.lower() in text_lower for rk in relevancy_keywords):
            continue
        if exclude_keywords and any(kw.lower() in text_lower for kw in exclude_keywords):
            continue
            
        if currency == "BRL":
            price_match = re.search(r'R\$\s*([\d\.]+(?:,\d{2})?)', content)
            if price_match:
                clean = price_match.group(1).replace('.', '').replace(',', '.')
                try:
                    val = float(clean)
                    items.append({
                        'title': text[:100],
                        'price': val,
                        'price_str': f"R$ {val:,.2f}",
                        'link': link,
                        'source': store_name
                    })
                except Exception:
                    pass
        else:
            price_match = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', content)
            if price_match:
                clean = price_match.group(1).replace(',', '')
                try:
                    val = float(clean)
                    items.append({
                        'title': text[:100],
                        'price': val,
                        'price_str': f"${val:,.2f}",
                        'link': link,
                        'source': store_name
                    })
                except Exception:
                    pass
    return items

def parse_store_page(store_name, html, relevancy_keywords, exclude_keywords, rate, currency="BRL"):
    name_lower = store_name.lower()
    if "mercado livre" in name_lower or "mercadolivre" in name_lower:
        return parse_mercadolivre(html, relevancy_keywords, exclude_keywords)
    elif "google" in name_lower or "shopping" in name_lower:
        return parse_google_shopping(html, relevancy_keywords, exclude_keywords)
    elif "amazon" in name_lower and ("br" in name_lower or "brasil" in name_lower or currency == "BRL"):
        return parse_amazon_br(html, relevancy_keywords, exclude_keywords)
    elif "pneustore" in name_lower:
        return parse_pneustore(html, relevancy_keywords, exclude_keywords)
    elif "ebay" in name_lower:
        return parse_ebay(html, relevancy_keywords, exclude_keywords, rate)
    elif "sweetwater" in name_lower:
        return parse_sweetwater(html, relevancy_keywords, exclude_keywords)
    elif "b&h" in name_lower or "bh" in name_lower:
        return parse_bh(html, relevancy_keywords, exclude_keywords)
    elif "guitar" in name_lower:
        return parse_guitarcenter(html, relevancy_keywords, exclude_keywords)
    elif "best buy" in name_lower or "bestbuy" in name_lower:
        return parse_bestbuy(html, relevancy_keywords, exclude_keywords)
    else:
        return parse_generic_store(html, store_name, relevancy_keywords, exclude_keywords, currency)

def parse_log_file(filepath):
    deals = []
    if not os.path.exists(filepath):
        return []
        
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    for line in lines:
        line = line.strip()
        if not line.startswith('|'):
            continue
            
        cells = [c.strip() for c in line.split('|')[1:-1]]
        if len(cells) < 5:
            continue
            
        date_str = cells[0]
        price_raw = cells[1]
        retailer = cells[2]
        details_raw = cells[3]
        status = cells[4]
        
        # Skip headers
        if "Date" in date_str or ":---" in date_str:
            continue
            
        # Parse title and link
        link = "N/A"
        title = details_raw
        link_match = re.search(r'\[([^\]]+)\]\(([^)]+)\)', details_raw)
        if link_match:
            title = link_match.group(1)
            link = link_match.group(2)
            
        # Extract numerical price for plotting
        usd_price = None
        if "N/A" not in price_raw:
            # Check if BRL with USD approximation (~$2,726.02)
            usd_approx_match = re.search(r'\(\~\$([\d,]+\.\d{2})\)', price_raw)
            if usd_approx_match:
                usd_price = float(usd_approx_match.group(1).replace(',', ''))
            else:
                clean_p = price_raw.replace('~', '').replace('R$', '').replace('$', '').strip()
                if ',' in clean_p and ('.' not in clean_p or clean_p.find('.') < clean_p.find(',')):
                    clean_p = clean_p.replace('.', '').replace(',', '.')
                else:
                    clean_p = clean_p.replace(',', '')
                m = re.search(r'([\d]+\.?\d*)', clean_p)
                if m:
                    try:
                        usd_price = float(m.group(1))
                    except:
                        pass
                    
        deals.append({
            'date': date_str,
            'price_raw': price_raw,
            'usd_price': usd_price,
            'retailer': retailer,
            'title': title,
            'link': link,
            'status': status
        })
        
    return deals

def format_datetime_br(dt_str):
    try:
        # Check if it has time
        if " " in dt_str:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%d/%m/%Y às %H:%M:%S")
        else:
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
            return dt.strftime("%d/%m/%Y")
    except Exception:
        return dt_str

def generate_dashboard():
    try:
        with open(DATA_JSON_FILE, 'r') as f:
            unified_data_json_str = f.read()
    except Exception as e:
        print(f"Error reading data.json for dashboard embedding: {e}")
        return
        
    unified_data = json.loads(unified_data_json_str)
    last_search_raw = unified_data.get("last_search_time", "N/A")
    last_search_time_formatted = format_datetime_br(last_search_raw)
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Caça-Preços Web</title>
    
    <!-- Outfit & Inter Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    
    <!-- FontAwesome Icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    
    <!-- Chart.js CDN -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: rgba(30, 41, 59, 0.45);
            --card-border: rgba(255, 255, 255, 0.08);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent-primary: #8b5cf6;
            --accent-secondary: #06b6d4;
            --accent-green: #10b981;
        }}
        
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        
        body {{
            background-color: var(--bg-color);
            background-image: 
                radial-gradient(at 0% 0%, rgba(139, 92, 246, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(6, 182, 212, 0.15) 0px, transparent 50%);
            background-attachment: fixed;
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            padding: 2rem;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2.5rem;
            animation: fadeInDown 0.6s ease-out;
            flex-wrap: wrap;
            gap: 1.5rem;
        }}
        
        .title-area h1 {{
            font-family: 'Outfit', sans-serif;
            font-size: 2.2rem;
            font-weight: 800;
            background: linear-gradient(135deg, #fff 30%, var(--text-secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
        }}
        
        .title-area p {{
            color: var(--text-secondary);
            font-size: 0.95rem;
        }}
        
        .grid-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
            animation: fadeInUp 0.7s ease-out;
        }}
        
        .card-stat {{
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.5rem;
            display: flex;
            align-items: center;
            gap: 1.25rem;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            text-decoration: none;
            color: inherit;
        }}
        
        .card-stat:hover {{
            transform: translateY(-5px);
            border-color: rgba(139, 92, 246, 0.3);
            box-shadow: 0 10px 20px -10px rgba(139, 92, 246, 0.2);
        }}
        
        .stat-icon {{
            width: 48px;
            height: 48px;
            border-radius: 12px;
            background: rgba(139, 92, 246, 0.15);
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--accent-primary);
            font-size: 1.25rem;
        }}
        
        .stat-icon.green {{
            background: rgba(16, 185, 129, 0.15);
            color: var(--accent-green);
        }}
        
        .stat-icon.cyan {{
            background: rgba(6, 182, 212, 0.15);
            color: var(--accent-secondary);
        }}
        
        .stat-info {{
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }}
        
        .stat-label {{
            color: var(--text-secondary);
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        
        .stat-val {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.5rem;
            font-weight: 700;
        }}
        
        .grid-charts {{
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 1.5rem;
            margin-bottom: 2rem;
            animation: fadeInUp 0.8s ease-out;
        }}
        
        @media (max-width: 900px) {{
            .grid-charts {{
                grid-template-columns: 1fr;
            }}
        }}
        
        .card-large {{
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            padding: 1.75rem;
        }}
        
        .card-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.15rem;
            font-weight: 600;
            margin-bottom: 1.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .card-title i {{
            color: var(--accent-primary);
        }}
        
        .chart-container {{
            position: relative;
            height: 300px;
            width: 100%;
        }}
        
        .card-best-deal {{
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }}
        
        .deal-badge {{
            background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
            padding: 0.35rem 0.75rem;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            width: fit-content;
            margin-bottom: 1rem;
        }}
        
        .deal-price-area {{
            margin-bottom: 1.5rem;
        }}
        
        .deal-price {{
            font-family: 'Outfit', sans-serif;
            font-size: 2.8rem;
            font-weight: 800;
            color: #fff;
            line-height: 1;
            margin-bottom: 0.25rem;
        }}
        
        .deal-retailer {{
            color: var(--accent-secondary);
            font-weight: 600;
            font-size: 1rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}
        
        .deal-title {{
            color: var(--text-secondary);
            font-size: 0.9rem;
            line-height: 1.4;
            margin-bottom: 1.5rem;
            flex-grow: 1;
        }}
        
        .btn-action {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            background: #fff;
            color: var(--bg-color);
            padding: 0.85rem 1.5rem;
            border-radius: 12px;
            text-decoration: none;
            font-weight: 600;
            font-size: 0.95rem;
            transition: all 0.2s ease;
            box-shadow: 0 4px 12px rgba(255, 255, 255, 0.1);
            border: none;
            cursor: pointer;
        }}
        
        .btn-action:hover {{
            background: var(--accent-primary);
            color: #fff;
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(139, 92, 246, 0.4);
        }}
        
        .btn-refresh {{
            padding: 0.5rem 1rem; 
            font-size: 0.85rem; 
            background: rgba(255,255,255,0.05); 
            color: #fff; 
            border: 1px solid var(--card-border); 
            box-shadow: none;
        }}
        
        .btn-refresh:hover {{
            background: rgba(255,255,255,0.1);
            color: #fff;
            box-shadow: none;
            transform: none;
        }}
        
        .btn-search-trigger {{
            padding: 0.5rem 1rem; 
            font-size: 0.85rem; 
            background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
            color: #fff;
            border: none;
            box-shadow: 0 4px 12px rgba(139, 92, 246, 0.2);
        }}
        
        .btn-search-trigger:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 15px rgba(139, 92, 246, 0.4);
        }}
        
        .btn-search-trigger:disabled {{
            background: var(--card-bg);
            color: var(--text-secondary);
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
            border: 1px solid var(--card-border);
        }}
        
        .card-table-area {{
            animation: fadeInUp 0.9s ease-out;
        }}
        
        .table-container {{
            width: 100%;
            overflow-x: auto;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}
        
        th {{
            color: var(--text-secondary);
            font-weight: 600;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            padding: 1rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }}
        
        td {{
            padding: 1rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            font-size: 0.9rem;
            color: var(--text-primary);
        }}
        
        tr {{
            transition: background 0.2s ease;
        }}
        
        tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
            color: #fff;
        }}
        
        .source-badge {{
            display: inline-flex;
            align-items: center;
            padding: 0.25rem 0.5rem;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
        
        .source-badge.ebay {{ background: rgba(244, 180, 0, 0.15); color: #f4b400; }}
        .source-badge.sweetwater {{ background: rgba(6, 182, 212, 0.15); color: var(--accent-secondary); }}
        .source-badge.guitar {{ background: rgba(139, 92, 246, 0.15); color: var(--accent-primary); }}
        .source-badge.bh {{ background: rgba(16, 185, 129, 0.15); color: var(--accent-green); }}
        .source-badge.bestbuy {{ background: rgba(59, 130, 246, 0.15); color: #3b82f6; }}
        .source-badge.none {{ background: rgba(148, 163, 184, 0.15); color: var(--text-secondary); }}
        
        .status-badge {{
            font-size: 0.75rem;
            font-weight: 500;
        }}
        
        .status-badge.met {{ color: var(--accent-green); }}
        .status-badge.searching {{ color: var(--text-secondary); }}
        
        .link-icon {{
            color: var(--accent-secondary);
            text-decoration: none;
            transition: color 0.2s;
        }}
        
        .link-icon:hover {{
            color: #fff;
        }}
        
        .last-search-sticker {{
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            background: rgba(30, 41, 59, 0.45);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 0.4rem 0.85rem;
            border-radius: 50px;
            font-size: 0.8rem;
            color: var(--text-secondary);
            font-weight: 500;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        }}
        
        .product-selector-container {{
            display: inline-flex;
            align-items: center;
            gap: 0.75rem;
            background: rgba(30, 41, 59, 0.45);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 0.4rem 1rem;
            border-radius: 50px;
            font-size: 0.9rem;
            color: var(--text-secondary);
            font-weight: 500;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        }}
        
        .product-selector-container label {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            color: var(--text-secondary);
            font-weight: 600;
        }}
        
        .product-selector-container select {{
            background: transparent;
            border: none;
            color: #fff;
            font-family: 'Outfit', sans-serif;
            font-size: 0.95rem;
            font-weight: 700;
            outline: none;
            cursor: pointer;
            padding-right: 0.5rem;
        }}
        
        .product-selector-container select option {{
            background: var(--bg-color);
            color: #fff;
        }}
        
        .status-dot {{
            position: relative;
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--accent-green);
        }}
        
        .status-dot::after {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            border-radius: 50%;
            background-color: var(--accent-green);
            animation: ping 1.5s cubic-bezier(0, 0, 0.2, 1) infinite;
        }}
        
        @keyframes ping {{
            75%, 100% {{
                transform: scale(2.5);
                opacity: 0;
            }}
        }}
        
        /* Modal & Management Styles */
        .modal-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(15, 23, 42, 0.75);
            backdrop-filter: blur(10px);
            z-index: 1000;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 1.5rem;
            animation: fadeInDown 0.3s ease-out;
        }}
        .modal-card {{
            background: #1e293b;
            border: 1px solid var(--card-border);
            border-radius: 20px;
            max-width: 720px;
            width: 100%;
            max-height: 90vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
        }}
        .modal-header {{
            padding: 1.25rem 1.5rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(30, 41, 59, 0.5);
        }}
        .tab-btn {{
            background: none;
            border: none;
            color: var(--text-secondary);
            font-family: 'Outfit', sans-serif;
            font-size: 1rem;
            font-weight: 600;
            padding: 0.5rem 1rem;
            cursor: pointer;
            border-radius: 8px;
            transition: all 0.2s ease;
        }}
        .tab-btn.active {{
            background: rgba(139, 92, 246, 0.15);
            color: #fff;
            border-bottom: 2px solid var(--accent-primary);
        }}
        .btn-currency-toggle {{
            flex: 1;
            padding: 0.65rem 0.5rem;
            border-radius: 10px;
            border: 1px solid var(--card-border);
            background: rgba(15, 23, 42, 0.4);
            color: var(--text-secondary);
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .btn-currency-toggle.active {{
            background: rgba(139, 92, 246, 0.2);
            border-color: var(--accent-primary);
            color: #fff;
        }}
        .badge-curr {{
            display: inline-flex;
            align-items: center;
            padding: 0.2rem 0.5rem;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 700;
        }}
        .badge-curr.brl {{ background: rgba(16, 185, 129, 0.15); color: var(--accent-green); }}
        .badge-curr.usd {{ background: rgba(6, 182, 212, 0.15); color: var(--accent-secondary); }}
        
        /* Switch Toggle */
        .switch {{
            position: relative;
            display: inline-block;
            width: 44px;
            height: 24px;
        }}
        .switch input {{
            opacity: 0;
            width: 0;
            height: 0;
        }}
        .slider {{
            position: absolute;
            cursor: pointer;
            top: 0; left: 0; right: 0; bottom: 0;
            background-color: rgba(148, 163, 184, 0.3);
            transition: .3s;
            border-radius: 24px;
        }}
        .slider:before {{
            position: absolute;
            content: "";
            height: 18px;
            width: 18px;
            left: 3px;
            bottom: 3px;
            background-color: white;
            transition: .3s;
            border-radius: 50%;
        }}
        input:checked + .slider {{
            background-color: var(--accent-green);
        }}
        input:checked + .slider:before {{
            transform: translateX(20px);
        }}
        
        /* Animations */
        @keyframes fadeInDown {{
            from {{ opacity: 0; transform: translateY(-20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        @keyframes fadeInUp {{
            from {{ opacity: 0; transform: translateY(20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-area">
                <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 0.5rem;">
                    <h1 style="margin-bottom: 0; display: flex; align-items: center; gap: 0.6rem;">
                        <i class="fa-solid fa-crosshairs" style="color: var(--accent-primary);"></i> Caça-Preços Web
                    </h1>
                    <div class="product-selector-container">
                        <label for="product-select"><i class="fa-solid fa-box"></i> Produto:</label>
                        <select id="product-select">
                            <!-- Populated dynamically -->
                        </select>
                    </div>
                    <button id="btn-open-manage" class="btn-action btn-refresh" style="display: inline-flex; align-items: center; gap: 0.5rem; font-weight: 600;">
                        <i class="fa-solid fa-list-check"></i> Gerenciar Produtos
                    </button>
                    <div class="last-search-sticker">
                        <span id="product-active-indicator" class="status-dot"></span>
                        <span>Última busca: {last_search_time_formatted}</span>
                    </div>
                </div>
                <p>Monitorando: <strong id="header-product-title" style="color: var(--accent-secondary); font-weight: 700;">Carregando...</strong> &bull; Rastreador Inteligente em Tempo Real</p>
            </div>
            <div style="display: flex; gap: 10px; align-items: center;">
                <a href="#" onclick="window.location.reload();" class="btn-action btn-refresh">
                    <i class="fa-solid fa-rotate-right"></i> Atualizar
                </a>
                <button id="btn-new-search" class="btn-action btn-search-trigger">
                    <i class="fa-solid fa-magnifying-glass"></i> Buscar Agora
                </button>
            </div>
        </header>

        <!-- Status banner -->
        <div id="search-status-banner" style="display: none; background: rgba(30, 41, 59, 0.6); backdrop-filter: blur(10px); border: 1px solid rgba(139, 92, 246, 0.3); border-radius: 16px; padding: 1.25rem; margin-bottom: 2rem; align-items: center; justify-content: space-between; animation: fadeInUp 0.5s ease; box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);">
            <div style="display: flex; align-items: center; gap: 1rem;">
                <div style="width: 40px; height: 40px; border-radius: 50%; background: rgba(139, 92, 246, 0.15); display: flex; align-items: center; justify-content: center; color: var(--accent-primary);">
                    <i class="fa-solid fa-circle-notch fa-spin" style="font-size: 1.2rem;"></i>
                </div>
                <div>
                    <div style="font-weight: 600; color: #fff; font-family: 'Outfit', sans-serif;">Busca em Andamento...</div>
                    <div style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.15rem;">O Safari está rastreando ofertas nos sites configurados para cada produto ativo.</div>
                </div>
            </div>
        </div>
        
        <!-- Settings Panel -->
        <div class="card-large" style="margin-bottom: 2rem; display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem; animation: fadeInUp 0.7s ease-out;">
            <!-- Target Price Form -->
            <div>
                <div style="font-family: 'Outfit', sans-serif; font-size: 1.05rem; font-weight: 600; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem;">
                    <i class="fa-solid fa-circle-dollar-to-slot" style="color: var(--accent-primary);"></i>
                    <span>Preço-Alvo de Alerta</span>
                </div>
                <div style="display: flex; gap: 10px; align-items: center;">
                    <div style="position: relative; flex-grow: 1;">
                        <span id="target-currency-symbol" style="position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">$</span>
                        <input type="number" id="input-target-price" step="10" min="1" style="width: 100%; padding: 0.6rem 0.6rem 0.6rem 28px; border-radius: 10px; border: 1px solid var(--card-border); background: rgba(15, 23, 42, 0.4); color: #fff; font-family: 'Inter', sans-serif; font-weight: 600; font-size: 0.95rem; outline: none; transition: border-color 0.2s;" onfocus="this.style.borderColor='var(--accent-primary)'" onblur="this.style.borderColor='var(--card-border)'">
                    </div>
                    <button id="btn-save-price" class="btn-action" style="padding: 0.6rem 1.2rem; font-size: 0.85rem; background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary)); box-shadow: none;">
                        Salvar Preço
                    </button>
                </div>
                <div style="margin-top: 0.5rem; font-size: 0.8rem; color: var(--text-secondary);">
                    <span id="target-price-brl" style="color: var(--accent-secondary); font-weight: 600;">-</span> (Dispara notificação quando atingido)
                </div>
            </div>
            
            <!-- Search Interval Form -->
            <div>
                <div style="font-family: 'Outfit', sans-serif; font-size: 1.05rem; font-weight: 600; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem;">
                    <i class="fa-solid fa-clock" style="color: var(--accent-secondary);"></i>
                    <span>Frequência de Rastreamento</span>
                </div>
                <div style="position: relative;">
                    <select id="select-search-interval" style="width: 100%; padding: 0.6rem; border-radius: 10px; border: 1px solid var(--card-border); background: rgba(15, 23, 42, 0.4); color: #fff; font-family: 'Inter', sans-serif; font-weight: 600; font-size: 0.95rem; outline: none; cursor: pointer;">
                        <option value="14:00_17:30">Duas vezes ao dia (14:00 e 17:30)</option>
                        <option value="1h">De 1 em 1 hora</option>
                        <option value="3h">De 3 em 3 horas</option>
                        <option value="6h">De 6 em 6 horas</option>
                        <option value="12h">De 12 em 12 horas</option>
                        <option value="manual">Manual (Apenas quando clicado)</option>
                    </select>
                </div>
                <div style="margin-top: 0.5rem; font-size: 0.8rem; color: var(--text-secondary);">
                    Status do agendador: <span id="scheduler-status-text" style="color: var(--accent-green); font-weight: 600;">Ativo</span> <span id="save-interval-spinner" style="display:none; margin-left: 0.5rem; color: var(--accent-secondary);"><i class="fa-solid fa-circle-notch fa-spin"></i> salvando...</span>
                </div>
            </div>
        </div>
        
        <div class="grid-stats">
            <a id="stat-current-card" class="card-stat">
                <div class="stat-icon"><i class="fa-solid fa-tag"></i></div>
                <div class="stat-info">
                    <span class="stat-label">Melhor Preço Atual</span>
                    <span class="stat-val" id="stat-current">N/A</span>
                    <span class="stat-subval" id="stat-current-brl" style="font-size: 0.9rem; color: var(--accent-secondary); font-weight: 600; margin-top: 0.15rem;">N/A</span>
                </div>
            </a>
            <a id="stat-lowest-card" class="card-stat">
                <div class="stat-icon green"><i class="fa-solid fa-arrow-down"></i></div>
                <div class="stat-info">
                    <span class="stat-label">Menor Preço Histórico</span>
                    <span class="stat-val" id="stat-lowest">N/A</span>
                    <span class="stat-subval" id="stat-lowest-brl" style="font-size: 0.9rem; color: var(--accent-green); font-weight: 600; margin-top: 0.15rem;">N/A</span>
                </div>
            </a>
            <div class="card-stat">
                <div class="stat-icon" style="background: rgba(244, 180, 0, 0.15); color: #f4b400;"><i class="fa-solid fa-money-bill-transfer"></i></div>
                <div class="stat-info">
                    <span class="stat-label">Dólar Hoje (USD/BRL)</span>
                    <span class="stat-val" id="stat-rate">N/A</span>
                    <span class="stat-subval" style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.15rem;">Cotação do dia</span>
                </div>
            </div>
            <div class="card-stat">
                <div class="stat-icon cyan"><i class="fa-solid fa-chart-line"></i></div>
                <div class="stat-info">
                    <span class="stat-label">Total de Buscas</span>
                    <span class="stat-val" id="stat-total">0</span>
                    <span class="stat-subval" style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.15rem;">Histórico completo</span>
                </div>
            </div>
        </div>
        
        <div class="grid-charts">
            <div class="card-large">
                <div class="card-title">
                    <span>Histórico de Preços (USD)</span>
                    <i class="fa-solid fa-chart-area"></i>
                </div>
                <div class="chart-container">
                    <canvas id="priceChart"></canvas>
                </div>
            </div>
            
            <div class="card-large card-best-deal">
                <div>
                    <div class="deal-badge">Melhor Oferta do Momento</div>
                    <div class="deal-price-area">
                        <div class="deal-price" id="offer-price">N/A</div>
                        <div id="offer-price-brl" style="font-size: 1.3rem; color: var(--accent-secondary); font-weight: 700; margin-top: 0.25rem; margin-bottom: 0.75rem;">N/A</div>
                        <div class="deal-retailer" id="offer-retailer">
                            <i class="fa-solid fa-store"></i> -
                        </div>
                    </div>
                    <div class="deal-title" id="offer-title">
                        Carregando ofertas...
                    </div>
                </div>
                <a id="offer-link-btn" target="_blank" class="btn-action" style="display: none;"><i class="fa-solid fa-arrow-up-right-from-square"></i> Ir para a Oferta</a>
            </div>
        </div>
        
        <div class="card-large card-table-area">
            <div class="card-title">
                <span>Histórico Completo</span>
                <i class="fa-solid fa-list"></i>
            </div>
            <div class="table-container">
                <table id="history-table">
                    <thead>
                        <tr>
                            <th>Data</th>
                            <th>Preço</th>
                            <th>Loja</th>
                            <th>Detalhes</th>
                            <th>Status</th>
                            <th>Link</th>
                        </tr>
                    </thead>
                    <tbody>
                        <!-- Injected by JS -->
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    
    <!-- Modal Gerenciador de Produtos -->
    <div id="modal-manage-products" class="modal-overlay" style="display: none;">
        <div class="modal-card">
            <!-- Modal Header -->
            <div class="modal-header">
                <div style="display: flex; gap: 0.75rem; align-items: center;">
                    <button type="button" id="tab-btn-list" class="tab-btn active">
                        <i class="fa-solid fa-boxes-stacked"></i> Produtos Cadastrados
                    </button>
                    <button type="button" id="tab-btn-new" class="tab-btn">
                        <i class="fa-solid fa-circle-plus"></i> + Novo Produto
                    </button>
                </div>
                <button type="button" id="modal-close-btn" style="background: none; border: none; color: var(--text-secondary); font-size: 1.3rem; cursor: pointer;">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </div>
            
            <!-- Tab 1: Product List -->
            <div id="tab-content-list" style="padding: 1.5rem; overflow-y: auto; flex-grow: 1;">
                <div id="manage-products-list" style="display: flex; flex-direction: column; gap: 0.75rem;">
                    <!-- Rendered dynamically by JS -->
                </div>
            </div>
            
            <!-- Tab 2: New Product Form -->
            <div id="tab-content-new" style="display: none; padding: 1.5rem; overflow-y: auto; flex-grow: 1;">
                <form id="form-new-product" style="display: flex; flex-direction: column; gap: 1.25rem;">
                    <div>
                        <label style="display: block; font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.4rem; font-weight: 600;">Nome do Produto:</label>
                        <input type="text" id="new-prod-name" placeholder="Ex: Pneu Michelin 205/55 R16, Sony WH-1000XM5, iPhone 15..." required style="width: 100%; padding: 0.65rem 0.85rem; border-radius: 10px; border: 1px solid var(--card-border); background: rgba(15, 23, 42, 0.6); color: #fff; font-size: 0.95rem; outline: none;">
                    </div>
                    
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
                        <div>
                            <label style="display: block; font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.4rem; font-weight: 600;">Tipo de Compra / Moeda:</label>
                            <div style="display: flex; gap: 0.5rem;">
                                <button type="button" id="btn-curr-brl" class="btn-currency-toggle active" onclick="setCurrencySelection('BRL')">
                                    🇧🇷 Real (R$)
                                </button>
                                <button type="button" id="btn-curr-usd" class="btn-currency-toggle" onclick="setCurrencySelection('USD')">
                                    🌐 Dólar (US$)
                                </button>
                            </div>
                        </div>
                        <div>
                            <label style="display: block; font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.4rem; font-weight: 600;">Preço-Alvo de Alerta:</label>
                            <div style="position: relative;">
                                <span id="new-prod-curr-symbol" style="position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: var(--text-secondary); font-weight: 600;">R$</span>
                                <input type="number" id="new-prod-price" placeholder="Ex: 450.00" step="1" min="1" required style="width: 100%; padding: 0.65rem 0.65rem 0.65rem 36px; border-radius: 10px; border: 1px solid var(--card-border); background: rgba(15, 23, 42, 0.6); color: #fff; font-size: 0.95rem; outline: none;">
                            </div>
                        </div>
                    </div>
                    
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                            <label style="font-size: 0.85rem; color: var(--text-secondary); font-weight: 600;">Lojas para Monitorar:</label>
                            <span id="stores-hint" style="font-size: 0.75rem; color: var(--accent-secondary);">Sugestões para compra nacional</span>
                        </div>
                        <div id="stores-checkboxes-container" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.75rem; background: rgba(15, 23, 42, 0.4); padding: 1rem; border-radius: 12px; border: 1px solid var(--card-border);">
                            <!-- Populated dynamically based on currency -->
                        </div>
                    </div>
                    
                    <div id="custom-stores-container" style="display: flex; flex-direction: column; gap: 0.5rem;">
                        <!-- Custom stores added dynamically -->
                    </div>
                    <button type="button" id="btn-add-custom-store" style="background: none; border: 1px dashed rgba(6, 182, 212, 0.4); color: var(--accent-secondary); border-radius: 10px; padding: 0.6rem; font-size: 0.85rem; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 0.5rem;">
                        <i class="fa-solid fa-plus"></i> Adicionar Outra Loja da Internet
                    </button>
                    
                    <!-- Advanced Options (Collapsible) -->
                    <div style="border-top: 1px solid rgba(255, 255, 255, 0.06); padding-top: 0.75rem;">
                        <button type="button" id="btn-toggle-advanced" style="background: none; border: none; color: var(--text-secondary); font-size: 0.85rem; cursor: pointer; display: flex; align-items: center; gap: 0.5rem;">
                            <i class="fa-solid fa-chevron-right" id="advanced-chevron"></i> Opções Avançadas (Filtros de Palavras-Chave)
                        </button>
                        <div id="advanced-options-panel" style="display: none; margin-top: 0.75rem; flex-direction: column; gap: 0.75rem;">
                            <div>
                                <label style="font-size: 0.8rem; color: var(--text-secondary);">Palavras a Excluir (separadas por vírgula):</label>
                                <input type="text" id="new-prod-exclude" placeholder="cabo, capa, suporte, manual, adesivo..." style="width: 100%; padding: 0.5rem; border-radius: 8px; border: 1px solid var(--card-border); background: rgba(15, 23, 42, 0.4); color: #fff; font-size: 0.85rem;">
                            </div>
                            <div>
                                <label style="font-size: 0.8rem; color: var(--text-secondary);">Palavras Obrigatórias de Relevância (separadas por vírgula):</label>
                                <input type="text" id="new-prod-relevancy" placeholder="Deixe em branco para usar as palavras do nome do produto" style="width: 100%; padding: 0.5rem; border-radius: 8px; border: 1px solid var(--card-border); background: rgba(15, 23, 42, 0.4); color: #fff; font-size: 0.85rem;">
                            </div>
                        </div>
                    </div>
                    
                    <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 0.5rem;">
                        <button type="button" id="btn-cancel-new" class="btn-action btn-refresh">Cancelar</button>
                        <button type="submit" id="btn-submit-new" class="btn-action" style="background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary)); color: #fff;">
                            Salvar e Ativar Monitoramento
                        </button>
                    </div>
                </form>
            </div>
        </div>
    </div>
    
    <script>
        const embeddedData = {unified_data_json_str};
        let chartInstance = null;
        let selectedCurrency = 'BRL';
        
        const STORE_PRESETS = {{
            'BRL': [
                {{ name: 'Mercado Livre', url: 'https://lista.mercadolivre.com.br/{{query}}', checked: true }},
                {{ name: 'Google Shopping', url: 'https://www.google.com/search?tbm=shop&q={{query}}&hl=pt-BR', checked: true }},
                {{ name: 'Amazon Brasil', url: 'https://www.amazon.com.br/s?k={{query}}', checked: true }},
                {{ name: 'PneuStore', url: 'https://www.pneustore.com.br/busca/?q={{query}}', checked: false }}
            ],
            'USD': [
                {{ name: 'eBay', url: 'https://www.ebay.com/sch/i.html?_nkw={{query}}&_sop=15', checked: true }},
                {{ name: 'Sweetwater', url: 'https://www.sweetwater.com/store/search?s={{query}}', checked: true }},
                {{ name: 'Amazon US', url: 'https://www.amazon.com/s?k={{query}}', checked: true }},
                {{ name: 'B&H Photo', url: 'https://www.bhphotovideo.com/c/search?Ntt={{query}}', checked: false }},
                {{ name: 'Guitar Center', url: 'https://www.guitarcenter.com/search?Ntt={{query}}', checked: false }},
                {{ name: 'Best Buy', url: 'https://www.bestbuy.com/site/searchpage.jsp?st={{query}}', checked: false }}
            ]
        }};
        
        function setCurrencySelection(curr) {{
            selectedCurrency = curr;
            const btnBrl = document.getElementById('btn-curr-brl');
            const btnUsd = document.getElementById('btn-curr-usd');
            const sym = document.getElementById('new-prod-curr-symbol');
            const hint = document.getElementById('stores-hint');
            
            if (curr === 'BRL') {{
                btnBrl.classList.add('active');
                btnUsd.classList.remove('active');
                sym.innerText = 'R$';
                hint.innerText = 'Sugestões para compra nacional';
            }} else {{
                btnUsd.classList.add('active');
                btnBrl.classList.remove('active');
                sym.innerText = '$';
                hint.innerText = 'Sugestões para compra internacional';
            }}
            renderStoreCheckboxes(curr);
        }}
        
        function renderStoreCheckboxes(curr) {{
            const container = document.getElementById('stores-checkboxes-container');
            container.innerHTML = '';
            const list = STORE_PRESETS[curr] || [];
            list.forEach((st) => {{
                const div = document.createElement('label');
                div.style.display = 'flex';
                div.style.alignItems = 'center';
                div.style.gap = '0.5rem';
                div.style.fontSize = '0.85rem';
                div.style.cursor = 'pointer';
                div.style.color = '#fff';
                div.innerHTML = `<input type="checkbox" class="store-checkbox" value="${{st.name}}" data-url="${{st.url}}" ${{st.checked ? 'checked' : ''}}> ${{st.name}}`;
                container.appendChild(div);
            }});
        }}
        
        function renderProduct(productId) {{
            const product = embeddedData.products[productId];
            if (!product) return;
            
            const currency = product.currency || 'USD';
            const isBrl = currency === 'BRL';
            const rate = embeddedData.usd_to_brl_rate;
            const targetPrice = product.target_price || 0;
            const bestDeal = product.best_deal;
            const deals = product.deals || [];
            
            // Update active status indicator in header
            const dot = document.getElementById('product-active-indicator');
            if (product.active !== false) {{
                dot.style.backgroundColor = 'var(--accent-green)';
                dot.title = "Produto ativo no monitoramento";
            }} else {{
                dot.style.backgroundColor = 'var(--text-secondary)';
                dot.title = "Produto pausado no monitoramento";
            }}
            
            // Update target price symbol and value
            document.getElementById('target-currency-symbol').innerText = isBrl ? 'R$' : '$';
            document.getElementById('input-target-price').value = targetPrice;
            if (isBrl) {{
                document.getElementById('target-price-brl').innerText = 'Moeda Nativa: Real (R$)';
            }} else {{
                const targetBrl = 'R$ ' + (targetPrice * rate).toLocaleString('pt-BR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
                document.getElementById('target-price-brl').innerText = 'Equivalente em BRL: ' + targetBrl;
            }}
            
            // Update title
            document.getElementById('header-product-title').innerText = product.name;
            
            // Calculate valid deals
            const validDeals = deals.filter(d => d.usd_price !== null || (d.price_raw && d.price_raw !== 'N/A'));
            
            // Get lowest deal
            let lowestDeal = null;
            if (deals.length > 0) {{
                const priced = deals.filter(d => d.usd_price !== null && d.usd_price > 0);
                if (priced.length > 0) {{
                    lowestDeal = priced.reduce((min, d) => d.usd_price < min.usd_price ? d : min, priced[0]);
                }}
            }}
            
            // Best deal formatting
            let bestPriceStr = bestDeal ? bestDeal.price_str : "N/A";
            let bestPriceBrlStr = "N/A";
            if (bestDeal) {{
                if (isBrl) {{
                    bestPriceBrlStr = "Moeda Nacional (R$)";
                }} else {{
                    bestPriceBrlStr = 'R$ ' + (bestDeal.price * rate).toLocaleString('pt-BR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
                }}
            }}
            
            document.getElementById('stat-current').innerText = bestPriceStr;
            document.getElementById('stat-current-brl').innerText = bestPriceBrlStr;
            
            // Link styling
            const currentCard = document.getElementById('stat-current-card');
            if (bestDeal && bestDeal.link && bestDeal.link !== 'N/A') {{
                currentCard.href = bestDeal.link;
                currentCard.target = "_blank";
                currentCard.style.cursor = "pointer";
                currentCard.style.pointerEvents = "auto";
            }} else {{
                currentCard.removeAttribute('href');
                currentCard.removeAttribute('target');
                currentCard.style.cursor = "default";
                currentCard.style.pointerEvents = "none";
            }}
            
            // Lowest price card
            let lowestPriceStr = "N/A";
            let lowestPriceBrlStr = "N/A";
            if (lowestDeal) {{
                lowestPriceStr = lowestDeal.price_raw;
                if (isBrl) {{
                    lowestPriceBrlStr = "Menor valor registrado";
                }} else {{
                    lowestPriceBrlStr = 'R$ ' + (lowestDeal.usd_price * rate).toLocaleString('pt-BR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
                }}
            }}
            document.getElementById('stat-lowest').innerText = lowestPriceStr;
            document.getElementById('stat-lowest-brl').innerText = lowestPriceBrlStr;
            
            const lowestCard = document.getElementById('stat-lowest-card');
            if (lowestDeal && lowestDeal.link && lowestDeal.link !== 'N/A') {{
                lowestCard.href = lowestDeal.link;
                lowestCard.target = "_blank";
                lowestCard.style.cursor = "pointer";
                lowestCard.style.pointerEvents = "auto";
            }} else {{
                lowestCard.removeAttribute('href');
                lowestCard.removeAttribute('target');
                lowestCard.style.cursor = "default";
                lowestCard.style.pointerEvents = "none";
            }}
            
            document.getElementById('stat-rate').innerText = 'R$ ' + rate.toLocaleString('pt-BR', {{ minimumFractionDigits: 4, maximumFractionDigits: 4 }});
            document.getElementById('stat-total').innerText = deals.length;
            
            // Right panel best deal
            const offerTitle = document.getElementById('offer-title');
            const offerPrice = document.getElementById('offer-price');
            const offerPriceBrl = document.getElementById('offer-price-brl');
            const offerRetailer = document.getElementById('offer-retailer');
            const offerLinkBtn = document.getElementById('offer-link-btn');
            
            if (bestDeal) {{
                offerTitle.innerText = bestDeal.title;
                offerPrice.innerText = bestDeal.price_str;
                offerPriceBrl.innerText = isBrl ? "Preço à vista / parcelado" : bestPriceBrlStr;
                offerRetailer.innerHTML = '<i class="fa-solid fa-store"></i> ' + bestDeal.source;
                
                if (bestDeal.link && bestDeal.link !== 'N/A') {{
                    offerLinkBtn.href = bestDeal.link;
                    offerLinkBtn.style.display = "inline-flex";
                }} else {{
                    offerLinkBtn.style.display = "none";
                }}
            }} else {{
                offerTitle.innerText = "Nenhuma oferta encontrada ainda.";
                offerPrice.innerText = "N/A";
                offerPriceBrl.innerText = "N/A";
                offerRetailer.innerHTML = '<i class="fa-solid fa-store"></i> -';
                offerLinkBtn.style.display = "none";
            }}
            
            // Table
            const tbody = document.querySelector('#history-table tbody');
            tbody.innerHTML = '';
            
            deals.slice().reverse().forEach(deal => {{
                const tr = document.createElement('tr');
                const storeBadge = '<span class="source-badge">' + deal.retailer + '</span>';
                const isMet = deal.status.toLowerCase().includes('met') || deal.status.toLowerCase().includes('atingido');
                const statusBadge = '<span class="status-badge ' + (isMet ? 'met' : 'searching') + '">' + deal.status + '</span>';
                const linkCol = deal.link && deal.link !== 'N/A' ? '<a href="' + deal.link + '" target="_blank" class="link-icon"><i class="fa-solid fa-arrow-up-right-from-square"></i></a>' : '-';
                
                let subPriceStr = '';
                if (!isBrl && deal.usd_price) {{
                    const bVal = deal.usd_price * rate;
                    subPriceStr = '<div style="font-size: 0.75rem; color: var(--accent-secondary); font-weight: 500; margin-top: 0.15rem;">R$ ' + bVal.toLocaleString('pt-BR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }}) + '</div>';
                }}
                
                tr.innerHTML = `
                    <td>${{deal.date}}</td>
                    <td style="font-weight: 600;">
                        <div>${{deal.price_raw}}</div>
                        ${{subPriceStr}}
                    </td>
                    <td>${{storeBadge}}</td>
                    <td style="max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${{deal.title}}</td>
                    <td>${{statusBadge}}</td>
                    <td style="text-align: center;">${{linkCol}}</td>
                `;
                tbody.appendChild(tr);
            }});
            
            renderChart(validDeals, rate, currency);
        }}
        
        function renderChart(validDeals, rate, currency) {{
            const isBrl = currency === 'BRL';
            const labels = validDeals.map(d => d.date.split(' ')[0]);
            const prices = validDeals.map(d => {{
                if (d.usd_price !== null && d.usd_price !== undefined) {{
                    return d.usd_price;
                }}
                const clean = (d.price_raw || '').replace(/[^0-9,\.]/g, '');
                return parseFloat(clean.replace(',', '.')) || 0;
            }});
            
            const canvas = document.getElementById('priceChart');
            const ctx = canvas.getContext('2d');
            if (chartInstance) {{
                chartInstance.destroy();
            }}
            
            const gradient = ctx.createLinearGradient(0, 0, 0, 300);
            gradient.addColorStop(0, 'rgba(139, 92, 246, 0.4)');
            gradient.addColorStop(1, 'rgba(139, 92, 246, 0.0)');
            
            chartInstance = new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: labels,
                    datasets: [{{
                        label: isBrl ? 'Preço (R$)' : 'Preço (USD)',
                        data: prices,
                        borderColor: '#8b5cf6',
                        borderWidth: 3,
                        backgroundColor: gradient,
                        fill: true,
                        tension: 0.4,
                        pointBackgroundColor: '#06b6d4',
                        pointBorderColor: '#fff',
                        pointBorderWidth: 2,
                        pointRadius: 5,
                        pointHoverRadius: 7
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ display: false }},
                        tooltip: {{
                            backgroundColor: '#1e293b',
                            titleColor: '#fff',
                            bodyColor: '#e2e8f0',
                            borderColor: 'rgba(255,255,255,0.1)',
                            borderWidth: 1,
                            padding: 12,
                            displayColors: false,
                            callbacks: {{
                                label: function(context) {{
                                    const deal = validDeals[context.dataIndex];
                                    if (isBrl) {{
                                        return ['Preço: ' + deal.price_raw + ' (' + deal.retailer + ')'];
                                    }} else {{
                                        const brlVal = deal.usd_price * rate;
                                        const brlStr = 'R$ ' + brlVal.toLocaleString('pt-BR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
                                        return [
                                            'Preço: ' + deal.price_raw + ' (' + deal.retailer + ')',
                                            'Equivalente hoje: ' + brlStr
                                        ];
                                    }}
                                }}
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{
                            grid: {{ display: false }},
                            ticks: {{ color: '#94a3b8', font: {{ family: 'Inter' }} }}
                        }},
                        y: {{
                            grid: {{ color: 'rgba(255, 255, 255, 0.05)' }},
                            ticks: {{ 
                                color: '#94a3b8', 
                                font: {{ family: 'Inter' }},
                                callback: function(value) {{ return (isBrl ? 'R$ ' : '$') + value; }}
                            }}
                        }}
                    }}
                }}
            }});
        }}
        
        function openManageModal(tabName) {{
            document.getElementById('modal-manage-products').style.display = 'flex';
            if (tabName === 'new') {{
                switchManageTab('new');
            }} else {{
                switchManageTab('list');
                renderManageList();
            }}
        }}
        
        function closeManageModal() {{
            document.getElementById('modal-manage-products').style.display = 'none';
        }}
        
        function switchManageTab(tab) {{
            const btnList = document.getElementById('tab-btn-list');
            const btnNew = document.getElementById('tab-btn-new');
            const contentList = document.getElementById('tab-content-list');
            const contentNew = document.getElementById('tab-content-new');
            
            if (tab === 'new') {{
                btnNew.classList.add('active');
                btnList.classList.remove('active');
                contentNew.style.display = 'block';
                contentList.style.display = 'none';
            }} else {{
                btnList.classList.add('active');
                btnNew.classList.remove('active');
                contentList.style.display = 'block';
                contentNew.style.display = 'none';
                renderManageList();
            }}
        }}
        
        function renderManageList() {{
            const listContainer = document.getElementById('manage-products-list');
            listContainer.innerHTML = '';
            const productIds = Object.keys(embeddedData.products);
            
            if (productIds.length === 0) {{
                listContainer.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 2rem;">Nenhum produto cadastrado. Clique em "+ Novo Produto" acima para começar.</div>';
                return;
            }}
            
            productIds.forEach(id => {{
                const p = embeddedData.products[id];
                const isBrl = p.currency === 'BRL';
                const row = document.createElement('div');
                row.style.background = 'rgba(15, 23, 42, 0.4)';
                row.style.border = '1px solid var(--card-border)';
                row.style.borderRadius = '14px';
                row.style.padding = '1rem 1.25rem';
                row.style.display = 'flex';
                row.style.alignItems = 'center';
                row.style.justifyContent = 'space-between';
                row.style.flexWrap = 'wrap';
                row.style.gap = '1rem';
                
                const currBadge = isBrl ? '<span class="badge-curr brl">R$ BRL</span>' : '<span class="badge-curr usd">US$ USD</span>';
                const targetDisplay = (isBrl ? 'R$ ' : '$') + (p.target_price || 0).toLocaleString('pt-BR');
                const isAct = p.active !== false;
                
                row.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 0.75rem;">
                        <div>
                            <div style="font-weight: 700; font-size: 1rem; color: #fff; font-family: 'Outfit', sans-serif;">${{p.name}}</div>
                            <div style="display: flex; gap: 0.5rem; align-items: center; margin-top: 0.25rem;">
                                ${{currBadge}}
                                <span style="font-size: 0.8rem; color: var(--text-secondary);">Alvo: <b>${{targetDisplay}}</b></span>
                            </div>
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 1.25rem;">
                        <div style="display: flex; align-items: center; gap: 0.5rem;">
                            <span style="font-size: 0.8rem; color: ${{isAct ? 'var(--accent-green)' : 'var(--text-secondary)'}}; font-weight: 600;">${{isAct ? 'Ativo' : 'Pausado'}}</span>
                            <label class="switch">
                                <input type="checkbox" ${{isAct ? 'checked' : ''}} onchange="toggleProductActive('${{id}}', this.checked)">
                                <span class="slider"></span>
                            </label>
                        </div>
                        <button type="button" onclick="deleteProduct('${{id}}', '${{p.name}}')" style="background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #ef4444; width: 34px; height: 34px; border-radius: 8px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.2s;" title="Excluir Produto">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    </div>
                `;
                listContainer.appendChild(row);
            }});
        }}
        
        async function toggleProductActive(productId, activeState) {{
            try {{
                const res = await fetch('/api/products/toggle-active', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ product_id: productId, active: activeState }})
                }});
                const data = await res.json();
                if (data.status === 'success') {{
                    embeddedData.products[productId].active = data.active;
                    renderManageList();
                    const sel = document.getElementById('product-select').value;
                    if (sel === productId) {{
                        renderProduct(productId);
                    }}
                }} else {{
                    alert("Erro ao alterar status: " + data.message);
                }}
            }} catch (err) {{
                alert("Erro ao se comunicar com o servidor: " + err);
            }}
        }}
        
        async function deleteProduct(productId, productName) {{
            if (!confirm(`Tem certeza que deseja remover o produto "${{productName}}"?`)) {{
                return;
            }}
            const deleteHistory = confirm(`Deseja também APAGAR todos os arquivos de histórico e dados acumulados de "${{productName}}"?\n\nClique em OK para APAGAR o histórico.\nClique em Cancelar para MANTER o histórico arquivado.`);
            
            try {{
                const res = await fetch('/api/products/delete', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ product_id: productId, delete_history: deleteHistory }})
                }});
                const data = await res.json();
                if (data.status === 'success') {{
                    delete embeddedData.products[productId];
                    initSelector();
                    renderManageList();
                }} else {{
                    alert("Erro ao excluir produto: " + data.message);
                }}
            }} catch (err) {{
                alert("Erro ao se comunicar com o servidor: " + err);
            }}
        }}
        
        function updateSchedulerStatusText(interval) {{
            const statusText = document.getElementById('scheduler-status-text');
            if (interval === 'manual') {{
                statusText.innerText = 'Desativado (Execução Manual)';
                statusText.style.color = 'var(--text-secondary)';
            }} else {{
                statusText.innerText = 'Ativo (' + getIntervalLabel(interval) + ')';
                statusText.style.color = 'var(--accent-green)';
            }}
        }}
        
        function getIntervalLabel(val) {{
            switch(val) {{
                case '14:00_17:30': return '14:00 & 17:30';
                case '1h': return 'De 1 em 1 hora';
                case '3h': return 'De 3 em 3 horas';
                case '6h': return 'De 6 em 6 horas';
                case '12h': return 'De 12 em 12 horas';
                default: return val;
            }}
        }}
        
        function initSelector() {{
            const select = document.getElementById('product-select');
            select.innerHTML = '';
            
            const productIds = Object.keys(embeddedData.products);
            productIds.forEach(id => {{
                const p = embeddedData.products[id];
                const opt = document.createElement('option');
                opt.value = id;
                const statusTag = p.active === false ? ' (Pausado)' : '';
                opt.innerText = `${{p.name}} [${{p.currency || 'USD'}}]${{statusTag}}`;
                select.appendChild(opt);
            }});
            
            select.addEventListener('change', (e) => {{
                renderProduct(e.target.value);
            }});
            
            const intervalSelect = document.getElementById('select-search-interval');
            if (embeddedData.search_interval) {{
                intervalSelect.value = embeddedData.search_interval;
                updateSchedulerStatusText(embeddedData.search_interval);
            }}
            
            if (productIds.length > 0) {{
                renderProduct(productIds[0]);
            }}
        }}
        
        // Save price listener
        const btnSavePrice = document.getElementById('btn-save-price');
        btnSavePrice.addEventListener('click', async () => {{
            const productId = document.getElementById('product-select').value;
            const newPrice = parseFloat(document.getElementById('input-target-price').value);
            if (isNaN(newPrice) || newPrice <= 0) {{
                alert("Por favor, insira um preço válido maior que zero.");
                return;
            }}
            
            btnSavePrice.disabled = true;
            btnSavePrice.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Salvando...';
            
            try {{
                const res = await fetch('/update-target-price', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ product_id: productId, target_price: newPrice }})
                }});
                const data = await res.json();
                if (data.status === 'success') {{
                    embeddedData.products[productId].target_price = newPrice;
                    renderProduct(productId);
                    alert("Preço-alvo atualizado com sucesso!");
                }} else {{
                    alert("Erro ao salvar preço: " + data.message);
                }}
            }} catch (err) {{
                alert("Erro ao se comunicar com o servidor: " + err);
            }} finally {{
                btnSavePrice.disabled = false;
                btnSavePrice.innerHTML = 'Salvar Preço';
            }}
        }});
        
        // Save interval listener
        const selectInterval = document.getElementById('select-search-interval');
        const spinner = document.getElementById('save-interval-spinner');
        selectInterval.addEventListener('change', async () => {{
            const newInterval = selectInterval.value;
            spinner.style.display = 'inline';
            selectInterval.disabled = true;
            
            try {{
                const res = await fetch('/update-interval', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ interval: newInterval }})
                }});
                const data = await res.json();
                if (data.status === 'success') {{
                    embeddedData.search_interval = newInterval;
                    updateSchedulerStatusText(newInterval);
                }} else {{
                    alert("Erro ao salvar frequência de busca: " + data.message);
                }}
            }} catch (err) {{
                alert("Erro de comunicação com o servidor: " + err);
            }} finally {{
                spinner.style.display = 'none';
                selectInterval.disabled = false;
            }}
        }});
        
        // Dynamic BRL label update on number input changes
        document.getElementById('input-target-price').addEventListener('input', (e) => {{
            const productId = document.getElementById('product-select').value;
            const p = embeddedData.products[productId];
            const isBrl = p && p.currency === 'BRL';
            const val = parseFloat(e.target.value);
            const rate = embeddedData.usd_to_brl_rate;
            if (isBrl) {{
                document.getElementById('target-price-brl').innerText = 'Moeda Nativa: Real (R$)';
            }} else {{
                if (!isNaN(val) && val > 0) {{
                    const brlVal = 'R$ ' + (val * rate).toLocaleString('pt-BR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
                    document.getElementById('target-price-brl').innerText = 'Equivalente em BRL: ' + brlVal;
                }} else {{
                    document.getElementById('target-price-brl').innerText = 'R$ 0,00';
                }}
            }}
        }});
        
        // Setup Modal Listeners
        document.getElementById('btn-open-manage').addEventListener('click', () => openManageModal('list'));
        document.getElementById('modal-close-btn').addEventListener('click', closeManageModal);
        document.getElementById('tab-btn-list').addEventListener('click', () => switchManageTab('list'));
        document.getElementById('tab-btn-new').addEventListener('click', () => switchManageTab('new'));
        document.getElementById('btn-cancel-new').addEventListener('click', () => switchManageTab('list'));
        
        // Toggle advanced options
        document.getElementById('btn-toggle-advanced').addEventListener('click', () => {{
            const panel = document.getElementById('advanced-options-panel');
            const chevron = document.getElementById('advanced-chevron');
            if (panel.style.display === 'none' || panel.style.display === '') {{
                panel.style.display = 'flex';
                chevron.classList.remove('fa-chevron-right');
                chevron.classList.add('fa-chevron-down');
            }} else {{
                panel.style.display = 'none';
                chevron.classList.remove('fa-chevron-down');
                chevron.classList.add('fa-chevron-right');
            }}
        }});
        
        // Add custom store field
        document.getElementById('btn-add-custom-store').addEventListener('click', () => {{
            const container = document.getElementById('custom-stores-container');
            const row = document.createElement('div');
            row.style.display = 'grid';
            row.style.gridTemplateColumns = '140px 1fr 34px';
            row.style.gap = '0.5rem';
            row.style.alignItems = 'center';
            row.innerHTML = `
                <input type="text" placeholder="Nome da Loja" class="custom-store-name" style="padding: 0.5rem; border-radius: 8px; border: 1px solid var(--card-border); background: rgba(15, 23, 42, 0.4); color: #fff; font-size: 0.85rem;">
                <input type="text" placeholder="https://loja.com.br/busca?q={{query}}" class="custom-store-url" style="padding: 0.5rem; border-radius: 8px; border: 1px solid var(--card-border); background: rgba(15, 23, 42, 0.4); color: #fff; font-size: 0.85rem;">
                <button type="button" onclick="this.parentElement.remove()" style="background: none; border: none; color: #ef4444; cursor: pointer; font-size: 1rem;"><i class="fa-solid fa-xmark"></i></button>
            `;
            container.appendChild(row);
        }});
        
        // Auto category suggestion when typing product name
        document.getElementById('new-prod-name').addEventListener('input', (e) => {{
            const text = e.target.value.toLowerCase();
            if (text.includes('pneu') || text.includes('aro') || text.includes('pirelli') || text.includes('michelin') || text.includes('goodyear')) {{
                setCurrencySelection('BRL');
                const pneuCheckbox = Array.from(document.querySelectorAll('.store-checkbox')).find(cb => cb.value === 'PneuStore');
                if (pneuCheckbox) pneuCheckbox.checked = true;
            }}
        }});
        
        // Form submit handler
        document.getElementById('form-new-product').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const name = document.getElementById('new-prod-name').value.trim();
            const targetPrice = parseFloat(document.getElementById('new-prod-price').value);
            if (!name) {{
                alert("Por favor, preencha o nome do produto.");
                return;
            }}
            if (isNaN(targetPrice) || targetPrice <= 0) {{
                alert("Por favor, informe um preço-alvo válido.");
                return;
            }}
            
            // Collect stores
            const stores = {{}};
            const queryEncoded = encodeURIComponent(name);
            document.querySelectorAll('.store-checkbox:checked').forEach(cb => {{
                const urlTemplate = cb.getAttribute('data-url');
                stores[cb.value] = urlTemplate.replace('{{query}}', queryEncoded);
            }});
            
            // Custom stores
            document.querySelectorAll('#custom-stores-container > div').forEach(div => {{
                const sName = div.querySelector('.custom-store-name').value.trim();
                let sUrl = div.querySelector('.custom-store-url').value.trim();
                if (sName && sUrl) {{
                    if (!sUrl.includes('{{query}}')) {{
                        sUrl += (sUrl.includes('?') ? '&q=' : '?q=') + queryEncoded;
                    }} else {{
                        sUrl = sUrl.replace('{{query}}', queryEncoded);
                    }}
                    stores[sName] = sUrl;
                }}
            }});
            
            if (Object.keys(stores).length === 0) {{
                alert("Selecione ou adicione ao menos uma loja para monitorar.");
                return;
            }}
            
            // Keywords
            const excludeInput = document.getElementById('new-prod-exclude').value.trim();
            const relevancyInput = document.getElementById('new-prod-relevancy').value.trim();
            const excludeKeywords = excludeInput ? excludeInput.split(',').map(s => s.trim()).filter(Boolean) : [];
            const relevancyKeywords = relevancyInput ? relevancyInput.split(',').map(s => s.trim()).filter(Boolean) : [];
            
            const submitBtn = document.getElementById('btn-submit-new');
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Cadastrando...';
            
            try {{
                const res = await fetch('/api/products/add', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        name: name,
                        currency: selectedCurrency,
                        target_price: targetPrice,
                        active: true,
                        stores: stores,
                        exclude_keywords: excludeKeywords,
                        relevancy_keywords: relevancyKeywords
                    }})
                }});
                const data = await res.json();
                if (data.status === 'success') {{
                    alert(`Produto "${{name}}" cadastrado com sucesso e ativado para monitoramento!`);
                    window.location.reload();
                }} else {{
                    alert("Erro ao cadastrar produto: " + data.message);
                }}
            }} catch (err) {{
                alert("Erro ao se comunicar com o servidor: " + err);
            }} finally {{
                submitBtn.disabled = false;
                submitBtn.innerHTML = 'Salvar e Ativar Monitoramento';
            }}
        }});
        
        const btnNewSearch = document.getElementById('btn-new-search');
        const statusBanner = document.getElementById('search-status-banner');
        
        async function checkSearchStatus() {{
            try {{
                const res = await fetch('/status');
                if (res.ok) {{
                    const data = await res.json();
                    if (data.is_searching) {{
                        setSearchingState(true);
                        setTimeout(checkSearchStatus, 2000);
                    }} else {{
                        if (btnNewSearch.disabled) {{
                            setSearchingState(false);
                            window.location.reload();
                        }}
                    }}
                }}
            }} catch (err) {{
                console.log("Local server offline or unreachable");
            }}
        }}
        
        function setSearchingState(isSearching) {{
            if (isSearching) {{
                btnNewSearch.disabled = true;
                btnNewSearch.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Buscando...';
                statusBanner.style.display = 'flex';
            }} else {{
                btnNewSearch.disabled = false;
                btnNewSearch.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> Buscar Agora';
                statusBanner.style.display = 'none';
            }}
        }}
        
        btnNewSearch.addEventListener('click', async () => {{
            setSearchingState(true);
            try {{
                const res = await fetch('/run-search', {{ method: 'POST' }});
                if (!res.ok) {{
                    throw new Error("HTTP error " + res.status);
                }}
                const data = await res.json();
                if (data.status === 'started' || data.status === 'already_running') {{
                    setTimeout(checkSearchStatus, 2000);
                }} else if (data.status === 'no_active_products') {{
                    alert(data.message);
                    setSearchingState(false);
                    openManageModal('list');
                }} else {{
                    alert("Erro ao iniciar busca: " + JSON.stringify(data));
                    setSearchingState(false);
                }}
            }} catch (err) {{
                alert("Não foi possível se comunicar com o servidor do Caça-Preços Web. Verifique se o script está rodando no terminal.");
                setSearchingState(false);
            }}
        }});
        
        document.addEventListener('DOMContentLoaded', () => {{
            initSelector();
            setCurrencySelection('BRL');
            checkSearchStatus();
        }});
    </script>
</body>
</html>
"""
    
    with open(DASHBOARD_FILE, 'w') as f:
        f.write(html_content)
    with open(INDEX_FILE, 'w') as f:
        f.write(html_content)
    print(f"[{datetime.now()}] Generated dashboard at {DASHBOARD_FILE} and {INDEX_FILE}")

def wait_for_internet():
    first_attempt = True
    while True:
        connected = False
        try:
            socket.setdefaulttimeout(3)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("8.8.8.8", 53))
            s.close()
            connected = True
        except Exception:
            try:
                socket.gethostbyname("google.com")
                connected = True
            except Exception:
                pass
                
        if connected:
            if not first_attempt:
                print(f"[{datetime.now()}] Internet connection detected! Resuming execution...")
            return True
            
        if first_attempt:
            print(f"[{datetime.now()}] No internet connection detected. Waiting for network signal...")
            first_attempt = False
            
        time.sleep(10)

def fetch_usd_brl_rate():
    print(f"[{datetime.now()}] Fetching USD-BRL exchange rate...")
    url = "https://economia.awesomeapi.com.br/last/USD-BRL"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            bid_str = data["USDBRL"]["bid"]
            rate = float(bid_str)
            print(f"[{datetime.now()}] USD-BRL rate fetched: {rate}")
            return rate
    except Exception as e:
        print(f"Error fetching exchange rate: {e}. Using fallback rate of 5.15")
        return 5.15

def git_push_update():
    print(f"[{datetime.now()}] Attempting automatic git push...")
    base_dir = "/Users/macstudio-maj/.gemini/antigravity/scratch/yamaha-yc73-tracker"
    
    if not os.path.exists(os.path.join(base_dir, ".git")):
        print("Git repository not initialized. Skipping auto git push.")
        return
        
    try:
        res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=base_dir, capture_output=True, text=True)
        if res.returncode != 0:
            print("No remote origin 'origin' configured. Skipping auto git push.")
            return
            
        subprocess.run(["git", "add", "dashboard.html", "index.html", "data.json", "products.json", "config.json", "deals.md"], cwd=base_dir)
        subprocess.run(["git", "add", "deals_*.md"], cwd=base_dir)
        
        status_res = subprocess.run(["git", "status", "--porcelain"], cwd=base_dir, capture_output=True, text=True)
        if not status_res.stdout.strip():
            print("No changes to commit.")
            return
            
        commit_msg = f"update: automatic price tracking sync {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=base_dir)
        
        push_res = subprocess.run(["git", "push", "origin", "main"], cwd=base_dir, capture_output=True, text=True)
        if push_res.returncode == 0:
            print(f"[{datetime.now()}] Git push successful!")
        else:
            print(f"Git push failed: {push_res.stderr.strip()}")
    except Exception as e:
        print(f"Error executing git commands: {e}")

def run_tracker():
    print(f"\n=======================================================")
    print(f"[{datetime.now()}] Caça-Preços Web - Execução Iniciada...")
    print(f"=======================================================")
    
    wait_for_internet()
    
    rate = fetch_usd_brl_rate()
    products = load_products()
    if not products:
        print("No products configured to track.")
        return
        
    config = load_config()
    unified_data = {
        "products": {},
        "usd_to_brl_rate": rate,
        "last_search_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "search_interval": config.get("search_interval", "14:00_17:30")
    }
    
    # Load existing data.json to preserve inactive products and history
    if os.path.exists(DATA_JSON_FILE):
        try:
            with open(DATA_JSON_FILE, 'r') as f:
                old_data = json.load(f)
                if "products" in old_data:
                    unified_data["products"] = old_data["products"]
        except Exception as e:
            print(f"Warning reading existing data.json: {e}")
            
    active_count = 0
    for prod in products:
        prod_id = prod["id"]
        prod_name = prod["name"]
        currency = prod.get("currency", "BRL").upper()
        target_price = float(prod.get("target_price", 0.0))
        is_active = prod.get("active", True)
        
        log_file = get_log_file(prod_id)
        
        # If product is not active, preserve its history and skip scraping
        if not is_active:
            print(f"[{datetime.now()}] Product '{prod_name}' is paused (active=False). Preserving history and skipping search.")
            deals = parse_log_file(log_file)
            prev_best = None
            if prod_id in unified_data["products"]:
                prev_best = unified_data["products"][prod_id].get("best_deal")
            unified_data["products"][prod_id] = {
                "name": prod_name,
                "currency": currency,
                "active": False,
                "target_price": target_price,
                "best_deal": prev_best,
                "deals": deals
            }
            continue
            
        active_count += 1
        relevancy_keywords = prod.get("relevancy_keywords", [])
        exclude_keywords = prod.get("exclude_keywords", [])
        
        # Build stores dict
        stores = prod.get("stores", {})
        if not stores and "search_queries" in prod:
            stores = {}
            for sname, sq in prod["search_queries"].items():
                encoded_q = quote_plus(sq)
                if sname in KNOWN_STORES:
                    stores[sname] = KNOWN_STORES[sname]["url"].format(query=encoded_q)
                else:
                    stores[sname] = f"https://www.google.com/search?q={encoded_q}"
                    
        min_price = prod.get("min_price", target_price * 0.15) if target_price > 0 else 0.0
        print(f"[{datetime.now()}] Tracking active product: {prod_name} ({currency}) across {len(stores)} stores...")
        
        pages = fetch_pages_dynamic(stores)
        all_deals = []
        for sname, html in pages.items():
            s_deals = parse_store_page(sname, html, relevancy_keywords, exclude_keywords, rate, currency)
            all_deals.extend(s_deals)
            
        valid_deals = [d for d in all_deals if d['price'] >= min_price]
        valid_deals.sort(key=lambda x: x['price'])
        best_deal = valid_deals[0] if valid_deals else None
        
        log_file = get_log_file(prod_id)
        log_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        best_price_str = best_deal['price_str'] if best_deal else "N/A"
        best_retailer = best_deal['source'] if best_deal else "None"
        best_details = f"[{best_deal['title']}]({best_deal['link']})" if best_deal else "No items found"
        status_str = "TARGET MET!" if best_deal and target_price > 0 and best_deal['price'] <= target_price else "Searching..."
        
        log_entry = f"| {log_time} | {best_price_str} | {best_retailer} | {best_details} | {status_str} |\n"
        if os.path.exists(log_file):
            with open(log_file, 'a') as f:
                f.write(log_entry)
        else:
            with open(log_file, 'w') as f:
                f.write(f"# {prod_name} Price Tracker\n\n")
                f.write("| Date (UTC-3) | Best Price | Retailer | Details / Link | Status |\n")
                f.write("| :--- | :--- | :--- | :--- | :--- |\n")
                f.write(log_entry)
                
        deals = parse_log_file(log_file)
        unified_data["products"][prod_id] = {
            "name": prod_name,
            "currency": currency,
            "active": True,
            "target_price": target_price,
            "best_deal": best_deal,
            "deals": deals
        }
        
        # macOS Notification
        if best_deal:
            price = best_deal['price']
            source = best_deal['source']
            notif_title = f"Caça-Preços Web ({prod_name})"
            if target_price > 0 and price <= target_price:
                notif_subtitle = "🔥 PREÇO-ALVO ATINGIDO!"
                notif_msg = f"Melhor preço: {best_deal['price_str']} no {source}! Abrindo Dashboard."
            else:
                notif_subtitle = f"Melhor preço: {best_deal['price_str']} ({source})"
                notif_msg = f"Oferta encontrada no {source}. Detalhes no Dashboard."
            applescript = f'display notification "{notif_msg}" with title "{notif_title}" subtitle "{notif_subtitle}" sound name "Glass"'
            subprocess.run(["osascript", "-e", applescript])
        else:
            applescript = f'display notification "Busca finalizada para {prod_name}. Nenhuma oferta encontrada hoje." with title "Caça-Preços Web ({prod_name})" sound name "Pop"'
            subprocess.run(["osascript", "-e", applescript])
            
    try:
        with open(DATA_JSON_FILE, 'w') as f:
            json.dump(unified_data, f, indent=2)
        print(f"[{datetime.now()}] Saved unified data to {DATA_JSON_FILE}")
    except Exception as e:
        print(f"Error saving data.json: {e}")
        
    generate_dashboard()
    git_push_update()

def trigger_background_search():
    global is_searching, last_run_time
    if is_searching:
        return
    is_searching = True
    try:
        run_tracker()
        last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        print(f"Error in background search: {e}")
    finally:
        is_searching = False

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Requested-With, Content-Type')
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b''
        
        if self.path == '/run-search':
            global is_searching
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            if is_searching:
                self.wfile.write(json.dumps({'status': 'already_running'}).encode('utf-8'))
            else:
                products = load_products()
                active_prods = [p for p in products if p.get("active", True)]
                if not active_prods:
                    self.wfile.write(json.dumps({'status': 'no_active_products', 'message': 'Nenhum produto ativo para busca no momento. Ative ou cadastre um produto no Gerenciador.'}).encode('utf-8'))
                else:
                    threading.Thread(target=trigger_background_search).start()
                    self.wfile.write(json.dumps({'status': 'started', 'active_count': len(active_prods)}).encode('utf-8'))
        elif self.path == '/api/products/add':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                params = json.loads(post_data.decode('utf-8'))
                prod, err = add_product(params)
                if prod:
                    self.wfile.write(json.dumps({'status': 'success', 'product': prod}).encode('utf-8'))
                else:
                    self.wfile.write(json.dumps({'status': 'error', 'message': err or 'Erro ao cadastrar produto'}).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
        elif self.path == '/api/products/toggle-active':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                params = json.loads(post_data.decode('utf-8'))
                product_id = params.get("product_id")
                is_active = params.get("active")
                success, new_state = toggle_product_active(product_id, is_active)
                if success:
                    self.wfile.write(json.dumps({'status': 'success', 'active': new_state}).encode('utf-8'))
                else:
                    self.wfile.write(json.dumps({'status': 'error', 'message': 'Produto não encontrado'}).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
        elif self.path == '/api/products/delete':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                params = json.loads(post_data.decode('utf-8'))
                product_id = params.get("product_id")
                del_hist = bool(params.get("delete_history", False))
                success = delete_product(product_id, del_hist)
                if success:
                    self.wfile.write(json.dumps({'status': 'success'}).encode('utf-8'))
                else:
                    self.wfile.write(json.dumps({'status': 'error', 'message': 'Produto não encontrado'}).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
        elif self.path == '/update-target-price':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                params = json.loads(post_data.decode('utf-8'))
                product_id = params.get("product_id")
                target_price = params.get("target_price")
                if product_id and target_price is not None:
                    success = update_product_target_price(product_id, float(target_price))
                    if success:
                        self.wfile.write(json.dumps({'status': 'success', 'message': 'Target price updated.'}).encode('utf-8'))
                    else:
                        self.wfile.write(json.dumps({'status': 'error', 'message': 'Product not found.'}).encode('utf-8'))
                else:
                    self.wfile.write(json.dumps({'status': 'error', 'message': 'Missing parameters.'}).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
        elif self.path == '/update-interval':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                params = json.loads(post_data.decode('utf-8'))
                interval = params.get("interval")
                if interval:
                    update_search_interval(interval)
                    self.wfile.write(json.dumps({'status': 'success', 'message': 'Search interval updated.'}).encode('utf-8'))
                else:
                    self.wfile.write(json.dumps({'status': 'error', 'message': 'Missing interval parameter.'}).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_GET(self):
        if self.path == '/':
            try:
                with open(DASHBOARD_FILE, 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html; charset=utf-8')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(f.read())
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(f"Error reading dashboard: {e}".encode('utf-8'))
        elif self.path == '/data.json':
            try:
                with open(DATA_JSON_FILE, 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json; charset=utf-8')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(f.read())
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))
        elif self.path == '/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            status = {
                'is_searching': is_searching,
                'last_run': last_run_time
            }
            self.wfile.write(json.dumps(status).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

def start_server():
    server = HTTPServer(('localhost', PORT), DashboardHandler)
    print(f"[{datetime.now()}] Servidor Caça-Preços Web rodando em http://localhost:{PORT}")
    
    # Start scheduler loop background thread
    threading.Thread(target=scheduler_loop, daemon=True).start()
    
    threading.Thread(target=trigger_background_search).start()
    
    time.sleep(1)
    subprocess.run(["open", f"http://localhost:{PORT}"])
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nParando servidor Caça-Preços Web...")
        server.server_close()

if __name__ == "__main__":
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('localhost', PORT))
        s.close()
        start_server()
    except OSError:
        s.close()
        print(f"[{datetime.now()}] Servidor Caça-Preços Web já está rodando na porta {PORT}.")
        print("Disparando busca na instância ativa...")
        
        import urllib.request
        try:
            req = urllib.request.Request(f"http://localhost:{PORT}/run-search", method='POST')
            with urllib.request.urlopen(req, timeout=5) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                print(f"Server response: {res_data}")
            subprocess.run(["open", f"http://localhost:{PORT}"])
        except Exception as e:
            print(f"Error communicating with running server: {e}")
            print("Please check if another process is using port 18888.")
