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
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_CONFIG_FILE = os.path.join(BASE_DIR, "products.json")
DATA_JSON_FILE = os.path.join(BASE_DIR, "data.json")
DASHBOARD_FILE = os.path.join(BASE_DIR, "dashboard.html")
INDEX_FILE = os.path.join(BASE_DIR, "index.html")

# State flags
is_searching = False
last_run_time = "N/A"

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

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
    profile = data.get("profile", "fauzer")
    
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
        "relevancy_keywords": relevancy,
        "profile": profile
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
        "deals": [],
        "profile": profile
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
        return os.path.join(BASE_DIR, "deals.md")
    else:
        return os.path.join(BASE_DIR, f"deals_{product_id}.md")

def fetch_pages_playwright(stores_dict):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"[{datetime.now()}] Playwright not installed in current environment.")
        return {}
        
    print(f"[{datetime.now()}] Fetching {len(stores_dict)} stores via Playwright Chromium...")
    result = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled'
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="pt-BR"
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            for name, raw_url in stores_dict.items():
                try:
                    print(f"[{datetime.now()}] Loading {name}: {raw_url[:80]}...")
                    page = context.new_page()
                    page.goto(raw_url, timeout=35000, wait_until="domcontentloaded")
                    page.wait_for_timeout(4000)
                    result[name] = page.content()
                    page.close()
                except Exception as e:
                    print(f"[{datetime.now()}] Error loading {name} with Playwright: {e}")
                    # Urllib fallback for this store
                    try:
                        import urllib.request
                        req = urllib.request.Request(raw_url, headers={
                            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                            'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7'
                        })
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            result[name] = resp.read().decode('utf-8', errors='ignore')
                    except Exception as e2:
                        print(f"[{datetime.now()}] HTTP fallback error for {name}: {e2}")
            browser.close()
    except Exception as e:
        print(f"[{datetime.now()}] Playwright execution error: {e}")
    return result

def fetch_pages_dynamic(stores_dict):
    if not stores_dict:
        return {}
        
    is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
    is_macos = sys.platform == "darwin"
    
    # On local macOS (interactive session), use Safari AppleScript
    if is_macos and not is_ci:
        try:
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
            if res.returncode == 0:
                parts = res.stdout.split("===DELIMITER===")
                result = {}
                for i, name in enumerate(store_names):
                    if i < len(parts):
                        result[name] = parts[i]
                return result
            else:
                print(f"AppleScript Error: {res.stderr}. Trying Playwright / HTTP fallback...")
        except Exception as e:
            print(f"Safari execution error: {e}. Trying Playwright / HTTP fallback...")
            
    # Try Playwright
    pw_res = fetch_pages_playwright(stores_dict)
    if pw_res:
        return pw_res
        
    # HTTP requests fallback
    print(f"[{datetime.now()}] Fetching stores via HTTP fallback...")
    res_fallback = {}
    import urllib.request
    for name, raw_url in stores_dict.items():
        try:
            req = urllib.request.Request(raw_url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7'
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                res_fallback[name] = resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"HTTP fallback error for {name}: {e}")
    return res_fallback

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
        price_offscreen = re.search(r'a-offscreen\">([^<]+)</span>', block)
        price_whole = re.search(r'class="a-price-whole">([\d\.,]+)', block)
        price_frac = re.search(r'class="a-price-fraction">(\d+)</span>', block)
        link_match = re.search(r'href="(/[^"?]+/dp/[^"/?]+|https://www\.amazon\.com\.br/[^"]*)"', block)
        
        price_val = None
        if price_offscreen:
            raw_p = price_offscreen.group(1)
            clean_p = re.sub(r'[^\d,\.]', '', raw_p)
            if ',' in clean_p and '.' in clean_p:
                clean_p = clean_p.replace('.', '').replace(',', '.')
            elif ',' in clean_p:
                clean_p = clean_p.replace(',', '.')
            try:
                price_val = float(clean_p)
            except Exception:
                pass
                
        if price_val is None and price_whole:
            whole = re.sub(r'[^\d]', '', price_whole.group(1))
            frac = price_frac.group(1) if price_frac else "00"
            try:
                price_val = float(f"{whole}.{frac}")
            except Exception:
                pass
                
        if title_match and price_val is not None and price_val > 0:
            title = title_match.group(1).strip()
            title_lower = title.lower()
            if relevancy_keywords and not any(rk.lower() in title_lower for rk in relevancy_keywords):
                continue
            if exclude_keywords and any(kw.lower() in title_lower for kw in exclude_keywords):
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

def get_now_br():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None)
    except Exception:
        pass
    from datetime import timedelta, timezone
    utc_now = datetime.now(timezone.utc)
    return (utc_now - timedelta(hours=3)).replace(tzinfo=None)

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

def encrypt_data_payload(json_str, pin):
    salt = os.urandom(16)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000)
    key = kdf.derive(pin.encode("utf-8"))
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    ciphertext = aesgcm.encrypt(iv, json_str.encode("utf-8"), None)
    return {
        "salt": base64.b64encode(salt).decode("utf-8"),
        "iv": base64.b64encode(iv).decode("utf-8"),
        "ciphertext": base64.b64encode(ciphertext).decode("utf-8")
    }

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
    
    config = load_config()
    profiles = config.get("profiles", [])
    if not profiles:
        access_pin = str(config.get("access_pin", "140608"))
        profiles = [
            {"id": "fauzer", "name": "Fauzer", "pin": access_pin, "is_admin": True}
        ]
        
    encrypted_payloads = []
    for prof in profiles:
        prof_id = prof["id"]
        prof_name = prof["name"]
        prof_pin = str(prof["pin"])
        prof_is_admin = bool(prof.get("is_admin", False))
        
        all_prods = unified_data.get("products", {})
        if prof_is_admin:
            prof_products = all_prods
            prof_default = unified_data.get("default_product")
            if not prof_default or prof_default not in prof_products:
                prof_default = list(prof_products.keys())[0] if prof_products else None
        else:
            prof_products = {
                pid: pdata for pid, pdata in all_prods.items()
                if pdata.get("profile") == prof_id
            }
            prof_default = list(prof_products.keys())[0] if prof_products else None
            
        prof_data = {
            "profile": {
                "id": prof_id,
                "name": prof_name,
                "is_admin": prof_is_admin
            },
            "all_profiles": [{"id": p["id"], "name": p["name"]} for p in profiles] if prof_is_admin else [{"id": prof_id, "name": prof_name}],
            "products": prof_products,
            "usd_to_brl_rate": unified_data.get("usd_to_brl_rate", 5.4),
            "last_search_time": unified_data.get("last_search_time", "N/A"),
            "search_interval": unified_data.get("search_interval", "3h"),
            "default_product": prof_default
        }
        
        enc_item = encrypt_data_payload(json.dumps(prof_data), prof_pin)
        encrypted_payloads.append(enc_item)
        
    encrypted_payloads_json = json.dumps(encrypted_payloads)
    encrypted_payload_json = json.dumps(encrypted_payloads[0]) if encrypted_payloads else "{}"
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Caça-Preços Web</title>
    
    <!-- Favicon -->
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'%3E%3Crect width='512' height='512' rx='128' fill='%230f172a'/%3E%3Cg transform='translate(64, 64) scale(0.75)'%3E%3Cpath fill='%238b5cf6' d='M256 0c17.7 0 32 14.3 32 32l0 10.4c93.7 13.9 167.7 88 181.6 181.6l10.4 0c17.7 0 32 14.3 32 32s-14.3 32-32 32l-10.4 0c-13.9 93.7-88 167.7-181.6 181.6l0 10.4c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-10.4C130.3 455.7 56.3 381.7 42.4 288L32 288c-17.7 0-32-14.3-32-32s14.3-32 32-32l10.4 0C56.3 130.3 130.3 56.3 224 42.4L224 32c0-17.7 14.3-32 32-32zM107.4 288c12.5 58.3 58.4 104.1 116.6 116.6l0-20.6c0-17.7 14.3-32 32-32s32 14.3 32 32l0 20.6c58.3-12.5 104.1-58.4 116.6-116.6L384 288c-17.7 0-32-14.3-32-32s14.3-32 32-32l20.6 0C392.1 165.7 346.3 119.9 288 107.4l0 20.6c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-20.6C165.7 119.9 119.9 165.7 107.4 224l20.6 0c17.7 0 32 14.3 32 32s-14.3 32-32 32l-20.6 0zM256 224a32 32 0 1 1 0 64 32 32 0 1 1 0-64z'/%3E%3C/g%3E%3C/svg%3E">
    <link rel="icon" type="image/svg+xml" href="favicon.svg">
    <link rel="alternate icon" href="favicon.ico">
    <link rel="apple-touch-icon" href="favicon.png">
    
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
    <!-- PIN Lock Screen Overlay -->
    <div id="pin-lock-overlay" style="position: fixed; inset: 0; z-index: 9999; background: #0f172a; background-image: radial-gradient(at 0% 0%, rgba(139, 92, 246, 0.25) 0px, transparent 50%), radial-gradient(at 100% 100%, rgba(6, 182, 212, 0.25) 0px, transparent 50%); display: flex; align-items: center; justify-content: center; padding: 1.5rem; transition: opacity 0.3s ease, visibility 0.3s ease;">
        <div style="width: 100%; max-width: 400px; background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 24px; padding: 2.5rem 2rem; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5); text-align: center;">
            <div style="width: 64px; height: 64px; margin: 0 auto 1.25rem; border-radius: 50%; background: linear-gradient(135deg, rgba(139, 92, 246, 0.2), rgba(6, 182, 212, 0.2)); border: 1px solid rgba(139, 92, 246, 0.4); display: flex; align-items: center; justify-content: center; font-size: 1.75rem; color: #a78bfa; box-shadow: 0 0 20px rgba(139, 92, 246, 0.25);">
                <i class="fa-solid fa-shield-halved"></i>
            </div>
            <h2 style="font-family: 'Outfit', sans-serif; font-size: 1.6rem; font-weight: 700; color: #fff; margin-bottom: 0.25rem;">Caça-Preços Web</h2>
            <p style="color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 1.75rem;">Acesso Familiar Restrito por Perfil</p>
            
            <form id="form-pin-lock" onsubmit="handlePinSubmit(event);">
                <div style="margin-bottom: 1.25rem;">
                    <label for="input-pin" style="display: block; font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); margin-bottom: 0.6rem;">Digite seu PIN de acesso</label>
                    <input type="password" id="input-pin" maxlength="10" inputmode="numeric" pattern="[0-9]*" autocomplete="current-password" autofocus placeholder="••••" style="width: 100%; padding: 0.9rem; text-align: center; font-size: 1.6rem; letter-spacing: 0.6rem; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.15); background: rgba(15, 23, 42, 0.6); color: #fff; outline: none; font-family: monospace; transition: all 0.2s;" onfocus="this.style.borderColor='var(--accent-primary)'; this.style.boxShadow='0 0 0 3px rgba(139, 92, 246, 0.2)';" onblur="this.style.borderColor='rgba(255, 255, 255, 0.15)'; this.style.boxShadow='none';">
                </div>
                
                <div style="display: flex; align-items: center; justify-content: center; gap: 0.5rem; margin-bottom: 1.5rem; font-size: 0.85rem; color: var(--text-secondary); cursor: pointer;" onclick="const cb = document.getElementById('cb-remember'); cb.checked = !cb.checked;">
                    <input type="checkbox" id="cb-remember" checked onclick="event.stopPropagation();" style="accent-color: var(--accent-primary); width: 16px; height: 16px; cursor: pointer;">
                    <span>Lembrar neste aparelho</span>
                </div>
                
                <button type="submit" id="btn-unlock-pin" style="width: 100%; padding: 0.85rem; border-radius: 12px; border: none; background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary)); color: #fff; font-family: 'Outfit', sans-serif; font-size: 1rem; font-weight: 700; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 0.5rem; transition: transform 0.1s, opacity 0.2s; box-shadow: 0 4px 14px rgba(139, 92, 246, 0.4);">
                    <i class="fa-solid fa-lock-open"></i> Entrar
                </button>
                
                <div id="pin-error-msg" style="display: none; margin-top: 1rem; padding: 0.6rem; border-radius: 8px; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #f87171; font-size: 0.85rem;">
                    <i class="fa-solid fa-triangle-exclamation"></i> PIN incorreto. Tente novamente.
                </div>
            </form>
        </div>
    </div>

    <div class="container" id="main-app-container" style="display: none;">
        <header>
            <div class="title-area">
                <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 0.5rem;">
                    <h1 style="margin-bottom: 0; display: flex; align-items: center; gap: 0.6rem;">
                        <i class="fa-solid fa-crosshairs" style="color: var(--accent-primary);"></i> Caça-Preços Web
                    </h1>
                    <div id="profile-badge-pill" style="display: inline-flex; align-items: center; gap: 0.45rem; padding: 0.35rem 0.85rem; border-radius: 999px; background: rgba(139, 92, 246, 0.15); border: 1px solid rgba(139, 92, 246, 0.35); font-size: 0.85rem; font-weight: 600; color: #c4b5fd;">
                        <i id="profile-badge-icon" class="fa-solid fa-user"></i>
                        <span id="profile-badge-text">Perfil</span>
                    </div>
                    <div id="admin-profile-filter-wrap" style="display: none; align-items: center; gap: 0.4rem; background: rgba(15, 23, 42, 0.5); border: 1px solid var(--card-border); padding: 0.3rem 0.75rem; border-radius: 10px;">
                        <label for="admin-profile-filter" style="font-size: 0.8rem; font-weight: 600; color: var(--text-secondary); display: flex; align-items: center; gap: 0.35rem; cursor: pointer;">
                            <i class="fa-solid fa-users" style="color: var(--accent-secondary);"></i> Perfil:
                        </label>
                        <select id="admin-profile-filter" style="background: transparent; border: none; color: #fff; font-size: 0.85rem; outline: none; cursor: pointer; font-weight: 600;">
                        </select>
                    </div>
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
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                <button id="btn-gh-settings" onclick="openGitHubModal();" class="btn-action" style="display: inline-flex; align-items: center; gap: 0.5rem; font-weight: 600; background: rgba(56, 189, 248, 0.12); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.25);" title="Configurar conexão GitHub para uso online">
                    <span id="gh-status-dot" style="width: 8px; height: 8px; border-radius: 50%; background: #64748b; display: inline-block;"></span>
                    <i class="fa-brands fa-github"></i> <span id="gh-badge-text">GitHub Online</span>
                </button>
                <a href="#" onclick="window.location.reload();" class="btn-action btn-refresh">
                    <i class="fa-solid fa-rotate-right"></i> Atualizar
                </a>
                <button id="btn-new-search" class="btn-action btn-search-trigger">
                    <i class="fa-solid fa-magnifying-glass"></i> Buscar Agora
                </button>
                <button id="btn-lock-session" onclick="lockSession();" class="btn-action" style="display: inline-flex; align-items: center; gap: 0.4rem; background: rgba(239, 68, 68, 0.12); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.25); font-weight: 600;" title="Trocar perfil ou bloquear acesso">
                    <i class="fa-solid fa-arrow-right-from-bracket"></i> Trocar Perfil
                </button>
            </div>
        </header>

        <!-- Empty Profile State Banner -->
        <div id="no-products-banner" style="display: none; background: rgba(30, 41, 59, 0.45); border: 1px dashed rgba(139, 92, 246, 0.4); border-radius: 16px; padding: 3rem 2rem; text-align: center; margin-bottom: 2rem; flex-direction: column; align-items: center; justify-content: center; gap: 1rem; animation: fadeInUp 0.5s ease;">
            <div style="width: 56px; height: 56px; border-radius: 50%; background: rgba(139, 92, 246, 0.15); display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: #a78bfa;">
                <i class="fa-solid fa-box-open"></i>
            </div>
            <div>
                <h3 id="no-products-title" style="font-family: 'Outfit', sans-serif; font-size: 1.25rem; font-weight: 700; color: #fff; margin-bottom: 0.25rem;">Nenhum produto cadastrado neste perfil</h3>
                <p id="no-products-desc" style="color: var(--text-secondary); font-size: 0.9rem; max-width: 480px; margin: 0 auto;">Olá! Você ainda não tem produtos cadastrados para monitoramento. Clique no botão abaixo para adicionar seu primeiro produto!</p>
            </div>
            <button type="button" onclick="openManageModal('new');" class="btn-action" style="background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary)); color: #fff; padding: 0.75rem 1.5rem; font-weight: 700; border-radius: 12px; cursor: pointer; display: inline-flex; align-items: center; gap: 0.5rem; font-size: 0.95rem;">
                <i class="fa-solid fa-circle-plus"></i> Cadastrar Meu Primeiro Produto
            </button>
        </div>

        <div id="dashboard-content-area">

        <!-- Status banner -->
        <div id="search-status-banner" style="display: none; background: rgba(30, 41, 59, 0.6); backdrop-filter: blur(10px); border: 1px solid rgba(139, 92, 246, 0.3); border-radius: 16px; padding: 1.25rem; margin-bottom: 2rem; align-items: center; justify-content: space-between; animation: fadeInUp 0.5s ease; box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);">
            <div style="display: flex; align-items: center; gap: 1rem;">
                <div style="width: 40px; height: 40px; border-radius: 50%; background: rgba(139, 92, 246, 0.15); display: flex; align-items: center; justify-content: center; color: var(--accent-primary);">
                    <i class="fa-solid fa-circle-notch fa-spin" style="font-size: 1.2rem;"></i>
                </div>
                <div>
                    <div id="search-status-title" style="font-weight: 600; color: #fff; font-family: 'Outfit', sans-serif;">Busca em Andamento...</div>
                    <div id="search-status-text" style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.15rem;">Rastreando ofertas nos sites configurados para cada produto ativo...</div>
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
        </div> <!-- End dashboard-content-area -->
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
                    
                    <div id="new-prod-profile-container" style="display: none;">
                        <label style="display: block; font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.4rem; font-weight: 600;">Vincular ao Perfil:</label>
                        <select id="new-prod-profile" style="width: 100%; padding: 0.65rem 0.85rem; border-radius: 10px; border: 1px solid var(--card-border); background: rgba(15, 23, 42, 0.6); color: #fff; font-size: 0.95rem; outline: none;">
                        </select>
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
    
    <!-- Modal Configuração GitHub Online -->
    <div id="modal-github-config" class="modal-overlay" style="display: none;">
        <div class="modal-card" style="max-width: 580px;">
            <div class="modal-header">
                <div style="display: flex; gap: 0.75rem; align-items: center;">
                    <span style="font-family: 'Outfit', sans-serif; font-size: 1.15rem; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 0.5rem;">
                        <i class="fa-brands fa-github" style="color: #38bdf8;"></i> Conexão GitHub Online
                    </span>
                </div>
                <button type="button" onclick="closeGitHubModal();" style="background: none; border: none; color: var(--text-secondary); font-size: 1.3rem; cursor: pointer;">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </div>
            <div style="padding: 1.5rem; display: flex; flex-direction: column; gap: 1.25rem;">
                <p style="font-size: 0.9rem; color: var(--text-secondary); line-height: 1.5; margin: 0;">
                    Para <strong>cadastrar produtos, alterar preços e disparar varreduras direto pelo GitHub Pages</strong> (sem precisar do Mac ligado), você só precisa informar seu Token Pessoal do GitHub uma única vez neste dispositivo.
                </p>

                <div style="background: rgba(15, 23, 42, 0.5); border: 1px solid rgba(56, 189, 248, 0.2); border-radius: 12px; padding: 1rem; display: flex; flex-direction: column; gap: 0.6rem;">
                    <span style="font-size: 0.85rem; font-weight: 600; color: #38bdf8; display: flex; align-items: center; gap: 0.4rem;">
                        <i class="fa-solid fa-circle-info"></i> Passo 1: Como gerar seu token gratuito (30 segundos)
                    </span>
                    <ol style="margin: 0; padding-left: 1.25rem; font-size: 0.82rem; color: var(--text-secondary); line-height: 1.6;">
                        <li>Clique no botão abaixo para abrir a página de criação de tokens do GitHub.</li>
                        <li>Marque as caixas <strong>repo</strong> (acesso ao repositório) e <strong>workflow</strong> (disparo de buscas).</li>
                        <li>Role até o final e clique em <em>Generate token</em>. Depois copie o código gerado.</li>
                    </ol>
                    <a href="https://github.com/settings/tokens/new?scopes=repo,workflow&description=CacaPrecos-Painel-Online" target="_blank" class="btn-action" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); text-align: center; justify-content: center; font-size: 0.85rem; padding: 0.55rem; text-decoration: none; margin-top: 0.25rem;">
                        <i class="fa-solid fa-arrow-up-right-from-square"></i> Abrir tela de criação de Token no GitHub
                    </a>
                </div>

                <div>
                    <label for="gh-token-input" style="font-size: 0.85rem; font-weight: 600; color: #fff; display: block; margin-bottom: 0.4rem;">
                        Passo 2: Cole seu Token aqui
                    </label>
                    <input type="password" id="gh-token-input" placeholder="ghp_... ou github_pat_..." style="width: 100%; padding: 0.75rem; border-radius: 10px; border: 1px solid var(--card-border); background: rgba(15, 23, 42, 0.6); color: #fff; font-size: 0.9rem; font-family: monospace;">
                    <span style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.3rem; display: block;">
                        O token fica guardado com segurança apenas na memória do seu navegador neste dispositivo (localStorage).
                    </span>
                </div>

                <div id="gh-test-result" style="display: none; padding: 0.75rem; border-radius: 10px; font-size: 0.85rem;"></div>

                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 0.5rem; flex-wrap: wrap; gap: 0.5rem;">
                    <button type="button" onclick="disconnectGitHubToken();" class="btn-action" style="background: rgba(239, 68, 68, 0.12); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.25); font-size: 0.85rem;">
                        <i class="fa-solid fa-trash"></i> Desconectar
                    </button>
                    <div style="display: flex; gap: 0.5rem;">
                        <button type="button" onclick="testGitHubConnection();" class="btn-action btn-refresh" style="font-size: 0.85rem;">
                            <i class="fa-solid fa-plug"></i> Testar Conexão
                        </button>
                        <button type="button" onclick="saveGitHubModalToken();" class="btn-action" style="background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary)); color: #fff; font-weight: 700; font-size: 0.85rem;">
                            <i class="fa-solid fa-check"></i> Salvar Token
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        const encryptedPayloads = {encrypted_payloads_json};
        const encryptedPayload = {encrypted_payload_json};
        let embeddedData = null;
        let currentProfile = null;
        let allAvailableProfiles = [];
        let activeProfileFilter = 'all';
        let chartInstance = null;
        let selectedCurrency = 'BRL';
        
        // ==========================================
        // GITHUB ONLINE INTEGRATION LAYER
        // ==========================================
        const GITHUB_REPO_OWNER = 'startagenciadigital';
        const GITHUB_REPO_NAME = 'caca-precos';
        const GITHUB_WORKFLOW_FILE = 'tracker.yml';

        function isOnlineMode() {{
            return window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1';
        }}

        function getGitHubToken() {{
            return localStorage.getItem('caca_precos_gh_token') || '';
        }}

        function openGitHubModal() {{
            const input = document.getElementById('gh-token-input');
            if (input) input.value = getGitHubToken();
            const resEl = document.getElementById('gh-test-result');
            if (resEl) resEl.style.display = 'none';
            document.getElementById('modal-github-config').style.display = 'flex';
        }}

        function closeGitHubModal() {{
            document.getElementById('modal-github-config').style.display = 'none';
        }}

        function saveGitHubModalToken() {{
            const input = document.getElementById('gh-token-input');
            const token = input ? input.value.trim() : '';
            if (!token) {{
                alert("Por favor, cole o token do GitHub antes de salvar.");
                return;
            }}
            localStorage.setItem('caca_precos_gh_token', token);
            updateGitHubUI();
            alert("Token do GitHub salvo com sucesso neste aparelho!");
            closeGitHubModal();
        }}

        function disconnectGitHubToken() {{
            if (confirm("Deseja remover o token do GitHub deste dispositivo?")) {{
                localStorage.removeItem('caca_precos_gh_token');
                const input = document.getElementById('gh-token-input');
                if (input) input.value = '';
                updateGitHubUI();
                alert("Token desconectado.");
                closeGitHubModal();
            }}
        }}

        async function testGitHubConnection() {{
            const input = document.getElementById('gh-token-input');
            const token = input ? input.value.trim() : getGitHubToken();
            const resEl = document.getElementById('gh-test-result');
            if (!token) {{
                alert("Insira um token para testar.");
                return;
            }}
            resEl.style.display = 'block';
            resEl.style.background = 'rgba(56, 189, 248, 0.15)';
            resEl.style.color = '#38bdf8';
            resEl.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Testando acesso ao repositório...';
            
            try {{
                const res = await fetch(`https://api.github.com/repos/${{GITHUB_REPO_OWNER}}/${{GITHUB_REPO_NAME}}`, {{
                    headers: {{
                        'Authorization': `Bearer ${{token}}`,
                        'Accept': 'application/vnd.github.v3+json'
                    }}
                }});
                if (res.ok) {{
                    const repoData = await res.json();
                    resEl.style.background = 'rgba(16, 185, 129, 0.15)';
                    resEl.style.color = '#10b981';
                    resEl.innerHTML = `<i class="fa-solid fa-circle-check"></i> Conexão bem-sucedida com <strong>${{repoData.full_name}}</strong>! Permissões verificadas.`;
                }} else {{
                    const err = await res.json().catch(() => ({{}}));
                    resEl.style.background = 'rgba(239, 68, 68, 0.15)';
                    resEl.style.color = '#f87171';
                    resEl.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Erro (${{res.status}}): ${{err.message || 'Token inválido ou sem permissão de acesso ao repositório.'}}`;
                }}
            }} catch (e) {{
                resEl.style.background = 'rgba(239, 68, 68, 0.15)';
                resEl.style.color = '#f87171';
                resEl.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Falha de rede: ${{e.message}}`;
            }}
        }}

        function updateGitHubUI() {{
            const badge = document.getElementById('gh-badge-text');
            const dot = document.getElementById('gh-status-dot');
            const token = getGitHubToken();
            if (!badge) return;
            if (token) {{
                badge.innerText = 'GitHub Online';
                if (dot) {{
                    dot.style.background = '#10b981';
                    dot.title = 'Conectado à nuvem GitHub Actions';
                }}
            }} else {{
                badge.innerText = isOnlineMode() ? 'Conectar GitHub' : 'GitHub (Local)';
                if (dot) {{
                    dot.style.background = isOnlineMode() ? '#f59e0b' : '#64748b';
                    dot.title = isOnlineMode() ? 'Necessário token para ações online' : 'Usando servidor Python local';
                }}
            }}
        }}

        async function promptGitHubTokenIfNeeded() {{
            const token = getGitHubToken();
            if (!token) {{
                openGitHubModal();
                throw new Error("TOKEN_REQUIRED");
            }}
            return token;
        }}

        async function fetchGitHubFile(path) {{
            const token = await promptGitHubTokenIfNeeded();
            const res = await fetch(`https://api.github.com/repos/${{GITHUB_REPO_OWNER}}/${{GITHUB_REPO_NAME}}/contents/${{path}}?ref=main&t=${{Date.now()}}`, {{
                headers: {{
                    'Authorization': `Bearer ${{token}}`,
                    'Accept': 'application/vnd.github.v3+json'
                }}
            }});
            if (!res.ok) {{
                const errData = await res.json().catch(() => ({{}}));
                throw new Error(errData.message || `Erro ${{res.status}} ao carregar ${{path}} do GitHub`);
            }}
            const data = await res.json();
            const binary = atob(data.content.replace(/\s/g, ''));
            const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
            const decodedStr = new TextDecoder('utf-8').decode(bytes);
            return {{
                sha: data.sha,
                content: JSON.parse(decodedStr)
            }};
        }}

        async function updateGitHubFile(path, newContentObj, sha, commitMsg) {{
            const token = await promptGitHubTokenIfNeeded();
            const jsonStr = JSON.stringify(newContentObj, null, 2);
            const bytes = new TextEncoder().encode(jsonStr);
            let binary = '';
            for (let i = 0; i < bytes.length; i++) {{
                binary += String.fromCharCode(bytes[i]);
            }}
            const base64Content = btoa(binary);
            
            const res = await fetch(`https://api.github.com/repos/${{GITHUB_REPO_OWNER}}/${{GITHUB_REPO_NAME}}/contents/${{path}}`, {{
                method: 'PUT',
                headers: {{
                    'Authorization': `Bearer ${{token}}`,
                    'Accept': 'application/vnd.github.v3+json',
                    'Content-Type': 'application/json'
                }},
                body: JSON.stringify({{
                    message: commitMsg,
                    content: base64Content,
                    sha: sha,
                    branch: 'main'
                }})
            }});
            if (!res.ok) {{
                const errData = await res.json().catch(() => ({{}}));
                throw new Error(errData.message || `Erro ${{res.status}} ao salvar no GitHub`);
            }}
            return await res.json();
        }}

        async function dispatchGitHubWorkflow() {{
            const token = await promptGitHubTokenIfNeeded();
            const res = await fetch(`https://api.github.com/repos/${{GITHUB_REPO_OWNER}}/${{GITHUB_REPO_NAME}}/actions/workflows/${{GITHUB_WORKFLOW_FILE}}/dispatches`, {{
                method: 'POST',
                headers: {{
                    'Authorization': `Bearer ${{token}}`,
                    'Accept': 'application/vnd.github.v3+json',
                    'Content-Type': 'application/json'
                }},
                body: JSON.stringify({{
                    ref: 'main'
                }})
            }});
            if (!res.ok) {{
                const errData = await res.json().catch(() => ({{}}));
                throw new Error(errData.message || `Erro ${{res.status}} ao acionar busca no GitHub Actions`);
            }}
            return true;
        }}

        async function getLatestWorkflowRun() {{
            const token = getGitHubToken();
            const headers = {{ 'Accept': 'application/vnd.github.v3+json' }};
            if (token) headers['Authorization'] = `Bearer ${{token}}`;
            
            const res = await fetch(`https://api.github.com/repos/${{GITHUB_REPO_OWNER}}/${{GITHUB_REPO_NAME}}/actions/workflows/${{GITHUB_WORKFLOW_FILE}}/runs?per_page=1&t=${{Date.now()}}`, {{
                headers: headers
            }});
            if (res.ok) {{
                const data = await res.json();
                if (data.workflow_runs && data.workflow_runs.length > 0) {{
                    return data.workflow_runs[0];
                }}
            }}
            return null;
        }}

        async function checkOnlineSearchStatus() {{
            try {{
                const run = await getLatestWorkflowRun();
                const statusBanner = document.getElementById('search-status-banner');
                const statusText = document.getElementById('search-status-text');
                const isSearchingTracked = sessionStorage.getItem('caca_precos_searching') === 'true';
                
                if (run) {{
                    if (run.status === 'in_progress' || run.status === 'queued') {{
                        setSearchingState(true);
                        if (statusText) {{
                            const stepName = run.status === 'queued' ? 'Na fila de execução' : 'Rastreando lojas na nuvem';
                            statusText.innerText = `GitHub Actions #${{run.run_number}}: ${{stepName}}...`;
                        }}
                        setTimeout(checkOnlineSearchStatus, 4000);
                    }} else if (run.status === 'completed') {{
                        if (isSearchingTracked || (btnNewSearch && btnNewSearch.disabled)) {{
                            sessionStorage.removeItem('caca_precos_searching');
                            setSearchingState(false);
                            if (run.conclusion === 'success') {{
                                alert("Varredura concluída com sucesso pelo GitHub Actions! Atualizando dados da página...");
                                window.location.reload();
                            }} else {{
                                alert(`A varredura no GitHub Actions finalizou com status: ${{run.conclusion}}. Você pode verificar os detalhes na aba Actions do repositório.`);
                            }}
                        }} else {{
                            setSearchingState(false);
                        }}
                    }}
                }} else {{
                    setSearchingState(false);
                }}
            }} catch (e) {{
                console.log("Status check notice:", e);
                setSearchingState(false);
            }}
        }}

        function slugifyJs(text) {{
            return text.toString().toLowerCase().trim()
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^a-z0-9]+/g, '-')
                .replace(/^-+|-+$/g, '');
        }}
        
        async function decryptPayload(pin, payload) {{
            const salt = Uint8Array.from(atob(payload.salt), c => c.charCodeAt(0));
            const iv = Uint8Array.from(atob(payload.iv), c => c.charCodeAt(0));
            const ciphertext = Uint8Array.from(atob(payload.ciphertext), c => c.charCodeAt(0));

            const keyMaterial = await crypto.subtle.importKey(
                "raw",
                new TextEncoder().encode(pin),
                {{ name: "PBKDF2" }},
                false,
                ["deriveKey"]
            );

            const key = await crypto.subtle.deriveKey(
                {{ name: "PBKDF2", salt, iterations: 100000, hash: "SHA-256" }},
                keyMaterial,
                {{ name: "AES-GCM", length: 256 }},
                false,
                ["decrypt"]
            );

            const decrypted = await crypto.subtle.decrypt(
                {{ name: "AES-GCM", iv }},
                key,
                ciphertext
            );

            return JSON.parse(new TextDecoder().decode(decrypted));
        }}
        
        async function attemptUnlock(pin, remember) {{
            const errorEl = document.getElementById('pin-error-msg');
            const btnUnlock = document.getElementById('btn-unlock-pin');
            if (errorEl) errorEl.style.display = 'none';
            if (btnUnlock) {{
                btnUnlock.disabled = true;
                btnUnlock.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Verificando...';
            }}
            
            try {{
                const payloads = Array.isArray(encryptedPayloads) ? encryptedPayloads : [encryptedPayloads];
                let data = null;
                for (const p of payloads) {{
                    try {{
                        data = await decryptPayload(pin, p);
                        if (data && data.profile) break;
                    }} catch (e) {{
                        // Continue trying next payload
                    }}
                }}
                
                if (!data || !data.products) {{
                    throw new Error("PIN incorreto");
                }}
                
                embeddedData = data;
                currentProfile = data.profile;
                allAvailableProfiles = data.all_profiles || [data.profile];
                activeProfileFilter = 'all';
                
                if (remember) {{
                    localStorage.setItem('caca_precos_pin', pin);
                }}
                
                const lockOverlay = document.getElementById('pin-lock-overlay');
                if (lockOverlay) {{
                    lockOverlay.style.opacity = '0';
                    setTimeout(() => {{
                        lockOverlay.style.display = 'none';
                    }}, 300);
                }}
                
                const mainContainer = document.getElementById('main-app-container');
                if (mainContainer) mainContainer.style.display = 'block';
                
                setupProfileUI();
                initSelector();
                setCurrencySelection('BRL');
                checkSearchStatus();
                return true;
            }} catch (err) {{
                if (errorEl) errorEl.style.display = 'block';
                if (btnUnlock) {{
                    btnUnlock.disabled = false;
                    btnUnlock.innerHTML = '<i class="fa-solid fa-lock-open"></i> Entrar';
                }}
                const inputPin = document.getElementById('input-pin');
                if (inputPin) {{
                    inputPin.value = '';
                    inputPin.focus();
                }}
                return false;
            }}
        }}
        
        function handlePinSubmit(e) {{
            e.preventDefault();
            const pin = document.getElementById('input-pin').value.trim();
            const remember = document.getElementById('cb-remember').checked;
            if (!pin) return;
            attemptUnlock(pin, remember);
        }}
        
        function lockSession() {{
            localStorage.removeItem('caca_precos_pin');
            window.location.reload();
        }}
        
        function setupProfileUI() {{
            const badgeText = document.getElementById('profile-badge-text');
            const badgeIcon = document.getElementById('profile-badge-icon');
            if (badgeText && currentProfile) {{
                if (currentProfile.is_admin) {{
                    badgeText.innerText = currentProfile.name + ' (Admin)';
                    if (badgeIcon) badgeIcon.className = 'fa-solid fa-crown';
                }} else {{
                    badgeText.innerText = currentProfile.name;
                    if (badgeIcon) badgeIcon.className = 'fa-solid fa-user';
                }}
            }}
            
            const filterWrap = document.getElementById('admin-profile-filter-wrap');
            const filterSelect = document.getElementById('admin-profile-filter');
            if (currentProfile && currentProfile.is_admin && allAvailableProfiles.length > 1) {{
                if (filterWrap) filterWrap.style.display = 'inline-flex';
                if (filterSelect) {{
                    filterSelect.innerHTML = '<option value="all">Todos os Perfis</option>';
                    allAvailableProfiles.forEach(p => {{
                        const opt = document.createElement('option');
                        opt.value = p.id;
                        opt.innerText = p.name;
                        filterSelect.appendChild(opt);
                    }});
                    filterSelect.value = activeProfileFilter;
                    filterSelect.onchange = (e) => {{
                        activeProfileFilter = e.target.value;
                        initSelector();
                        if (document.getElementById('modal-manage-products').style.display === 'flex') {{
                            renderManageList();
                        }}
                    }};
                }}
            }} else {{
                if (filterWrap) filterWrap.style.display = 'none';
            }}
            
            const newProdProfileContainer = document.getElementById('new-prod-profile-container');
            const newProdProfileSelect = document.getElementById('new-prod-profile');
            if (newProdProfileContainer && newProdProfileSelect) {{
                if (currentProfile && currentProfile.is_admin && allAvailableProfiles.length > 1) {{
                    newProdProfileContainer.style.display = 'block';
                    newProdProfileSelect.innerHTML = '';
                    allAvailableProfiles.forEach(p => {{
                        const opt = document.createElement('option');
                        opt.value = p.id;
                        opt.innerText = p.name;
                        if (p.id === currentProfile.id) opt.selected = true;
                        newProdProfileSelect.appendChild(opt);
                    }});
                }} else {{
                    newProdProfileContainer.style.display = 'none';
                }}
            }}
        }}

        function getFilteredProductIds() {{
            if (!embeddedData || !embeddedData.products) return [];
            const allIds = Object.keys(embeddedData.products);
            if (!currentProfile || !currentProfile.is_admin || activeProfileFilter === 'all') {{
                return allIds;
            }}
            return allIds.filter(id => embeddedData.products[id].profile === activeProfileFilter);
        }}
        
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
            const productIds = getFilteredProductIds();
            
            if (productIds.length === 0) {{
                listContainer.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 2rem;">Nenhum produto cadastrado para este perfil. Clique em "+ Novo Produto" acima para começar.</div>';
                return;
            }}
            
            const currentDefault = localStorage.getItem('caca_precos_default_product') || embeddedData.default_product || (productIds.find(pid => embeddedData.products[pid].active !== false) || productIds[0]);
            
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
                const ownerBadge = (currentProfile && currentProfile.is_admin && p.profile) ? `<span style="display: inline-flex; align-items: center; gap: 0.25rem; font-size: 0.72rem; font-weight: 700; color: #c4b5fd; background: rgba(139, 92, 246, 0.15); border: 1px solid rgba(139, 92, 246, 0.3); padding: 0.15rem 0.5rem; border-radius: 6px;"><i class="fa-solid fa-user"></i> ${{p.profile}}</span>` : '';
                const targetDisplay = (isBrl ? 'R$ ' : '$') + (p.target_price || 0).toLocaleString('pt-BR');
                const isAct = p.active !== false;
                const isDefault = (id === currentDefault);
                
                row.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 0.75rem;">
                        <div>
                            <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                                <span style="font-weight: 700; font-size: 1rem; color: #fff; font-family: 'Outfit', sans-serif;">${{p.name}}</span>
                                ${{ownerBadge}}
                                ${{isDefault ? '<span style="display: inline-flex; align-items: center; gap: 0.25rem; font-size: 0.72rem; font-weight: 700; color: #fbbf24; background: rgba(251, 191, 36, 0.15); border: 1px solid rgba(251, 191, 36, 0.3); padding: 0.15rem 0.5rem; border-radius: 6px;"><i class="fa-solid fa-star"></i> Padrão</span>' : ''}}
                            </div>
                            <div style="display: flex; gap: 0.5rem; align-items: center; margin-top: 0.25rem; flex-wrap: wrap;">
                                ${{currBadge}}
                                <span style="font-size: 0.8rem; color: var(--text-secondary);">Alvo: <b>${{targetDisplay}}</b></span>
                            </div>
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.85rem; flex-wrap: wrap;">
                        <button type="button" onclick="setDefaultProduct('${{id}}')" style="display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.45rem 0.85rem; border-radius: 9px; font-size: 0.8rem; font-weight: 600; cursor: pointer; transition: all 0.2s; ${{isDefault ? 'background: rgba(139, 92, 246, 0.25); color: #c4b5fd; border: 1px solid rgba(139, 92, 246, 0.6);' : 'background: rgba(255, 255, 255, 0.05); color: var(--text-secondary); border: 1px solid rgba(255, 255, 255, 0.12);'}}" title="Definir para o app sempre abrir com esse produto">
                            <i class="${{isDefault ? 'fa-solid fa-star' : 'fa-regular fa-star'}}" style="${{isDefault ? 'color: #fbbf24;' : ''}}"></i>
                            <span>${{isDefault ? 'Abrindo por padrão' : 'Abrir com esse produto'}}</span>
                        </button>
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
        
        async function setDefaultProduct(productId) {{
            localStorage.setItem('caca_precos_default_product', productId);
            if (embeddedData) {{
                embeddedData.default_product = productId;
            }}
            renderManageList();
            
            const select = document.getElementById('product-select');
            if (select) {{
                select.value = productId;
            }}
            renderProduct(productId);
            
            try {{
                await fetch('/api/products/set-default', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ product_id: productId }})
                }});
            }} catch (e) {{
                // static GitHub Pages
            }}
            
            setTimeout(() => {{
                closeManageModal();
            }}, 350);
        }}
        
        async function toggleProductActive(productId, activeState) {{
            if (isOnlineMode()) {{
                try {{
                    const fileData = await fetchGitHubFile('products.json');
                    const productsList = fileData.content;
                    const prod = productsList.find(p => p.id === productId);
                    if (prod) {{
                        prod.active = activeState;
                        await updateGitHubFile('products.json', productsList, fileData.sha, `update(products): alternar status de ${{productId}} para ${{activeState}}`);
                    }}
                    if (embeddedData && embeddedData.products && embeddedData.products[productId]) {{
                        embeddedData.products[productId].active = activeState;
                    }}
                    renderManageList();
                    const sel = document.getElementById('product-select').value;
                    if (sel === productId) {{
                        renderProduct(productId);
                    }}
                    alert("Status do produto atualizado com sucesso no GitHub!");
                }} catch (err) {{
                    if (err.message !== "TOKEN_REQUIRED") {{
                        alert("Erro ao alterar status online: " + err.message);
                    }}
                }}
                return;
            }}
            
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
            
            if (isOnlineMode()) {{
                try {{
                    const fileData = await fetchGitHubFile('products.json');
                    const productsList = fileData.content.filter(p => p.id !== productId);
                    await updateGitHubFile('products.json', productsList, fileData.sha, `refactor(products): remover ${{productName}} (${{productId}})`);
                    if (embeddedData && embeddedData.products) {{
                        delete embeddedData.products[productId];
                    }}
                    initSelector();
                    renderManageList();
                    alert(`Produto "${{productName}}" removido com sucesso no GitHub!`);
                }} catch (err) {{
                    if (err.message !== "TOKEN_REQUIRED") {{
                        alert("Erro ao excluir produto online: " + err.message);
                    }}
                }}
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
            
            const productIds = getFilteredProductIds();
            const noProductsBanner = document.getElementById('no-products-banner');
            const dashboardContentArea = document.getElementById('dashboard-content-area');
            const titleEl = document.getElementById('header-product-title');
            
            if (productIds.length === 0) {{
                const opt = document.createElement('option');
                opt.value = '';
                opt.innerText = '(Nenhum produto cadastrado)';
                select.appendChild(opt);
                select.disabled = true;
                if (noProductsBanner) {{
                    noProductsBanner.style.display = 'flex';
                    const noTitle = document.getElementById('no-products-title');
                    const noDesc = document.getElementById('no-products-desc');
                    if (currentProfile && !currentProfile.is_admin) {{
                        if (noTitle) noTitle.innerText = `Nenhum produto cadastrado para ${{currentProfile.name}}`;
                        if (noDesc) noDesc.innerText = `Olá, ${{currentProfile.name}}! Você ainda não tem produtos cadastrados para monitoramento. Clique no botão abaixo para adicionar seu primeiro item!`;
                    }}
                }}
                if (dashboardContentArea) dashboardContentArea.style.display = 'none';
                if (titleEl) titleEl.innerText = 'Nenhum produto cadastrado';
                return;
            }}
            
            select.disabled = false;
            if (noProductsBanner) noProductsBanner.style.display = 'none';
            if (dashboardContentArea) dashboardContentArea.style.display = 'block';
            
            productIds.forEach(id => {{
                const p = embeddedData.products[id];
                const opt = document.createElement('option');
                opt.value = id;
                const statusTag = p.active === false ? ' (Pausado)' : '';
                const ownerTag = (currentProfile && currentProfile.is_admin && p.profile) ? ` [${{p.profile}}]` : '';
                opt.innerText = `${{p.name}} [${{p.currency || 'USD'}}]${{ownerTag}}${{statusTag}}`;
                select.appendChild(opt);
            }});
            
            select.onchange = (e) => {{
                renderProduct(e.target.value);
            }};
            
            const intervalSelect = document.getElementById('select-search-interval');
            if (embeddedData.search_interval && intervalSelect) {{
                intervalSelect.value = embeddedData.search_interval;
                updateSchedulerStatusText(embeddedData.search_interval);
            }}
            
            const savedDefault = localStorage.getItem('caca_precos_default_product');
            let initialProduct = null;
            if (savedDefault && productIds.includes(savedDefault)) {{
                initialProduct = savedDefault;
            }} else if (embeddedData.default_product && productIds.includes(embeddedData.default_product)) {{
                initialProduct = embeddedData.default_product;
            }} else {{
                const firstActive = productIds.find(id => embeddedData.products[id].active !== false);
                initialProduct = firstActive || productIds[0];
            }}
            select.value = initialProduct;
            renderProduct(initialProduct);
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
            
            if (isOnlineMode()) {{
                try {{
                    const fileData = await fetchGitHubFile('products.json');
                    const productsList = fileData.content;
                    const prod = productsList.find(p => p.id === productId);
                    if (prod) {{
                        prod.target_price = newPrice;
                        await updateGitHubFile('products.json', productsList, fileData.sha, `update(price): alterar preço-alvo de ${{productId}} para ${{newPrice}}`);
                    }}
                    if (embeddedData && embeddedData.products && embeddedData.products[productId]) {{
                        embeddedData.products[productId].target_price = newPrice;
                    }}
                    renderProduct(productId);
                    alert("Preço-alvo atualizado com sucesso no GitHub!");
                }} catch (err) {{
                    if (err.message !== "TOKEN_REQUIRED") {{
                        alert("Erro ao salvar preço online: " + err.message);
                    }}
                }} finally {{
                    btnSavePrice.disabled = false;
                    btnSavePrice.innerHTML = 'Salvar Preço';
                }}
                return;
            }}
            
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
            
            const profSel = document.getElementById('new-prod-profile');
            const targetProfile = (currentProfile && currentProfile.is_admin && profSel && profSel.value) ? profSel.value : (currentProfile ? currentProfile.id : 'fauzer');
            
            if (isOnlineMode()) {{
                try {{
                    const fileData = await fetchGitHubFile('products.json');
                    const productsList = Array.isArray(fileData.content) ? fileData.content : [];
                    
                    const prodId = slugifyJs(name);
                    const newProd = {{
                        id: prodId,
                        name: name,
                        currency: selectedCurrency,
                        target_price: targetPrice,
                        active: true,
                        exclude_keywords: excludeKeywords,
                        relevancy_keywords: relevancyKeywords,
                        stores: stores,
                        search_queries: {{}},
                        profile: targetProfile
                    }};
                    for (const [sname, surl] of Object.entries(stores)) {{
                        newProd.search_queries[sname] = name;
                    }}
                    
                    const existingIdx = productsList.findIndex(p => p.id === prodId);
                    if (existingIdx >= 0) {{
                        productsList[existingIdx] = newProd;
                    }} else {{
                        productsList.push(newProd);
                    }}
                    
                    await updateGitHubFile('products.json', productsList, fileData.sha, `feat(products): cadastrar ${{name}} via painel online`);
                    
                    // Dispatch GitHub Actions workflow
                    try {{
                        await dispatchGitHubWorkflow();
                    }} catch (e) {{
                        console.log("Auto workflow dispatch notice:", e);
                    }}
                    
                    alert(`Produto "${{name}}" cadastrado com sucesso no GitHub! O robô do GitHub Actions já foi acionado.`);
                    window.location.reload();
                }} catch (err) {{
                    if (err.message !== "TOKEN_REQUIRED") {{
                        alert("Erro ao cadastrar produto online: " + err.message);
                    }}
                }} finally {{
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = 'Salvar e Ativar Monitoramento';
                }}
                return;
            }}
            
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
                        relevancy_keywords: relevancyKeywords,
                        profile: targetProfile
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
            if (isOnlineMode()) {{
                try {{
                    setSearchingState(true);
                    sessionStorage.setItem('caca_precos_searching', 'true');
                    const statusText = document.getElementById('search-status-text');
                    if (statusText) statusText.innerText = 'Acionando busca no GitHub Actions...';
                    await dispatchGitHubWorkflow();
                    if (statusText) statusText.innerText = 'Varredura iniciada no GitHub Actions! Aguardando robô...';
                    setTimeout(checkOnlineSearchStatus, 4000);
                }} catch (err) {{
                    setSearchingState(false);
                    sessionStorage.removeItem('caca_precos_searching');
                    if (err.message !== "TOKEN_REQUIRED") {{
                        alert("Erro ao disparar busca online: " + err.message);
                    }}
                }}
                return;
            }}
            
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
        
        document.addEventListener('DOMContentLoaded', async () => {{
            updateGitHubUI();
            
            if (isOnlineMode()) {{
                checkOnlineSearchStatus();
            }}
            
            const savedPin = localStorage.getItem('caca_precos_pin');
            if (savedPin) {{
                const success = await attemptUnlock(savedPin, true);
                if (!success) {{
                    localStorage.removeItem('caca_precos_pin');
                }}
            }}
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
    base_dir = BASE_DIR
    
    if not os.path.exists(os.path.join(base_dir, ".git")):
        print("Git repository not initialized. Skipping auto git push.")
        return
        
    try:
        res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=base_dir, capture_output=True, text=True)
        if res.returncode != 0:
            print("No remote origin 'origin' configured. Skipping auto git push.")
            return
            
        # Ensure git user is configured if not set
        user_res = subprocess.run(["git", "config", "user.name"], cwd=base_dir, capture_output=True, text=True)
        if not user_res.stdout.strip():
            subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=base_dir)
            subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=base_dir)
            
        subprocess.run(["git", "add", "dashboard.html", "index.html", "data.json", "products.json", "config.json", "deals.md", "favicon.svg", "favicon.ico", "favicon.png"], cwd=base_dir)
        subprocess.run(["git", "add", "deals_*.md"], cwd=base_dir)
        
        status_res = subprocess.run(["git", "status", "--porcelain"], cwd=base_dir, capture_output=True, text=True)
        if not status_res.stdout.strip():
            print("No changes to commit.")
            return
            
        commit_msg = f"update: automatic price tracking sync {get_now_br().strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=base_dir)
        
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=base_dir)
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
        "last_search_time": get_now_br().strftime("%Y-%m-%d %H:%M:%S"),
        "search_interval": config.get("search_interval", "14:00_17:30"),
        "default_product": config.get("default_product")
    }
    
    # Load existing data.json to preserve inactive products and history
    if os.path.exists(DATA_JSON_FILE):
        try:
            with open(DATA_JSON_FILE, 'r') as f:
                old_data = json.load(f)
                if "products" in old_data:
                    unified_data["products"] = old_data["products"]
                if "default_product" in old_data and not unified_data.get("default_product"):
                    unified_data["default_product"] = old_data["default_product"]
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
                "deals": deals,
                "profile": prod.get("profile", "fauzer")
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
        log_time = get_now_br().strftime("%Y-%m-%d %H:%M:%S")
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
            "deals": deals,
            "profile": prod.get("profile", "fauzer")
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
        if sys.platform == "darwin" and not os.environ.get("GITHUB_ACTIONS"):
            try:
                if best_deal and best_deal['price'] > 0:
                    applescript = f'display notification "{notif_msg}" with title "{notif_title}" subtitle "{notif_subtitle}" sound name "Glass"'
                else:
                    applescript = f'display notification "Busca finalizada para {prod_name}. Nenhuma oferta encontrada hoje." with title "Caça-Preços Web ({prod_name})" sound name "Pop"'
                subprocess.run(["osascript", "-e", applescript])
            except Exception:
                pass
            
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
        last_run_time = get_now_br().strftime("%Y-%m-%d %H:%M:%S")
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
        elif self.path == '/api/products/set-default':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                params = json.loads(post_data.decode('utf-8'))
                product_id = params.get("product_id")
                if product_id:
                    config = load_config()
                    config["default_product"] = product_id
                    save_config(config)
                    if os.path.exists(DATA_JSON_FILE):
                        with open(DATA_JSON_FILE, 'r') as f:
                            djson = json.load(f)
                        djson['default_product'] = product_id
                        with open(DATA_JSON_FILE, 'w') as f:
                            json.dump(djson, f, indent=2)
                    generate_dashboard()
                    git_push_update()
                    self.wfile.write(json.dumps({'status': 'success', 'default_product': product_id}).encode('utf-8'))
                else:
                    self.wfile.write(json.dumps({'status': 'error', 'message': 'Missing product_id'}).encode('utf-8'))
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
        elif self.path in ('/favicon.ico', '/favicon.png', '/favicon.svg'):
            fname = self.path.lstrip('/')
            fpath = os.path.join(os.path.dirname(DASHBOARD_FILE), fname)
            if os.path.exists(fpath):
                ctype = 'image/svg+xml' if fname.endswith('.svg') else ('image/png' if fname.endswith('.png') else 'image/x-icon')
                self.send_response(200)
                self.send_header('Content-type', ctype)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                with open(fpath, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
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
    if "--run-once" in sys.argv or os.environ.get("GITHUB_ACTIONS"):
        print(f"[{datetime.now()}] Iniciando execução única (--run-once)...")
        run_tracker()
        sys.exit(0)
        
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
