from flask import Flask, render_template, request, jsonify
import folium
import requests
import threading
import webbrowser
from geopy.distance import geodesic
import time
import gspread
from google.oauth2.service_account import Credentials
import random
import os
import csv
import google.generativeai as genai
from dotenv import load_dotenv
import logging
from datetime import datetime

load_dotenv()
app = Flask(__name__)

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


# ==============================
# 1. GOOGLE SHEET CONFIG
# ==============================
SHEET_ID = "1inrbMAXd3CXE0kK8QA_tFY8kIhU7V1L8ZwrgWAqndzY"   # <<< Thay bằng ID Google Sheet của bạn
CREDS_FILE = "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Cache chống spam API khi reload quá nhanh - Tăng thời gian cache lên 120s
_last_cache = {
    "timestamp": 0,
    "lat": None,
    "lon": None,
    "radius": None,
    "data": None
}

# Performance metrics
_performance_stats = {
    "total_requests": 0,
    "cache_hits": 0,
    "api_calls": 0,
    "avg_response_time": 0,
    "last_reset": time.time()
}

# Khoảng cách tối thiểu để coi là vị trí mới (100m)
MIN_LOCATION_CHANGE = 0.001  # ~100m in degrees

def is_location_similar(lat1, lon1, lat2, lon2):
    """Kiểm tra xem 2 vị trí có gần nhau không"""
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return False
    return abs(lat1 - lat2) < MIN_LOCATION_CHANGE and abs(lon1 - lon2) < MIN_LOCATION_CHANGE

# function to fetch data from Google Sheets (not used in current version)
def write_to_sheet(places):
    """Ghi toàn bộ dữ liệu địa điểm vào Google Sheets."""
    try:
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).sheet1

        sheet.clear()
        sheet.append_row(["Name", "Distance (m)", "Cuisine", "Phone", "Website", "Email", "Address"])

        for p in places:
            sheet.append_row([
                p["name"],
                p["distance"],
                p["cuisine"],
                p["phone"],
                p["website"],
                p["email"],
                p["address"]
            ])

    except Exception as e:
        print("Lỗi Google Sheets:", e)


# Configure Gemini API
api_key = os.getenv('GEMINI_API_KEY')
if api_key:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
else:
    model = None
    print("Warning: GEMINI_API_KEY not found in environment")

# Load restaurant data from CSV với validation
def load_restaurants_from_csv(csv_file='datasheet.csv'):
    start_time = time.time()
    restaurants = []
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            total_rows = 0
            valid_rows = 0
            for row in reader:
                total_rows += 1
                # Validate và clean data
                if row.get('Name') and row.get('Name').strip() != 'Unknown':
                    # Convert distance to int if possible
                    try:
                        row['Distance (m)'] = int(float(row.get('Distance (m)', 0)))
                    except (ValueError, TypeError):
                        row['Distance (m)'] = 0
                    restaurants.append(row)
                    valid_rows += 1
        
        # Sắp xếp theo khoảng cách
        restaurants.sort(key=lambda x: x.get('Distance (m)', 999999))
        
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"✅ Loaded {valid_rows}/{total_rows} valid restaurants from {csv_file} in {elapsed:.1f}ms")
    except FileNotFoundError:
        logger.warning(f"⚠️ Warning: {csv_file} not found")
    except Exception as e:
        logger.error(f"❌ Error loading CSV: {e}")
    return restaurants

restaurant_data = load_restaurants_from_csv()

#functions to get nearby places from OpenStreetMap
def get_nearby_places(lat, lon, radius=2000, limit=30):
    """Lấy quán ăn gần đây với giới hạn số lượng.
    
    Args:
        lat: Vĩ độ
        lon: Kinh độ  
        radius: Bán kính tìm kiếm (m) - mặc định 2km
        limit: Số lượng kết quả tối đa - mặc định 30
    """
    global _last_cache, _performance_stats
    
    start_time = time.time()
    _performance_stats["total_requests"] += 1
    
    logger.info(f"📍 Search request: lat={lat:.6f}, lon={lon:.6f}, radius={radius}m, limit={limit}")

    # Cache 120 giây (2 phút) và kiểm tra vị trí tương tự
    now = time.time()
    cache_age = now - _last_cache["timestamp"]
    
    if (_last_cache["data"] is not None 
        and cache_age <= 120
        and is_location_similar(lat, lon, _last_cache["lat"], _last_cache["lon"])
        and _last_cache["radius"] == radius):

        _performance_stats["cache_hits"] += 1
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"⚡ CACHE HIT (age: {cache_age:.1f}s) - Response time: {elapsed:.1f}ms")
        logger.info(f"📊 Stats - Total: {_performance_stats['total_requests']}, Cache hits: {_performance_stats['cache_hits']} ({_performance_stats['cache_hits']/_performance_stats['total_requests']*100:.1f}%)")
        return _last_cache["data"][:limit]

    overpass_url = "http://overpass-api.de/api/interpreter"
    # Tối ưu query: chỉ lấy tags cần thiết và giới hạn kết quả
    overpass_query = f"""
    [out:json][timeout:10];
    (
      node["amenity"="restaurant"](around:{radius},{lat},{lon});
      node["amenity"="cafe"](around:{radius},{lat},{lon});
      node["amenity"="bar"](around:{radius},{lat},{lon});
    );
    out body center {limit * 2};
    """

    _performance_stats["api_calls"] += 1
    logger.info(f"🌎 API Call #{_performance_stats['api_calls']} to Overpass API...")
    api_start = time.time()

    # Retry logic - thử 3 lần nếu API lỗi
    max_retries = 3
    retry_delay = 1
    
    for attempt in range(max_retries):
        try:
            response = requests.get(
                overpass_url,
                params={'data': overpass_query},
                timeout=15,
                headers={'User-Agent': 'NearBuyFood/1.0'}
            )
            api_elapsed = (time.time() - api_start) * 1000
            logger.info(f"✅ API response received in {api_elapsed:.1f}ms (status: {response.status_code})")
            break
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                logger.warning(f"⏱️ Timeout on attempt {attempt + 1}/{max_retries}, retrying...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                logger.error("❌ Request timeout after all retries - using cache")
                return _last_cache.get("data", []) if _last_cache["data"] else []
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"⚠️ Request failed on attempt {attempt + 1}: {str(e)[:100]}")
                time.sleep(retry_delay)
            else:
                logger.error(f"❌ Request failed after all retries: {str(e)[:100]}")
                return _last_cache.get("data", []) if _last_cache["data"] else []

    # Không phải status 200 → API lỗi
    if response.status_code != 200:
        logger.error(f"❌ Overpass API Error: {response.status_code}")
        logger.debug(f"Response preview: {response.text[:300]}")
        return []

    # Thử parse JSON
    try:
        data = response.json()
        elements_count = len(data.get('elements', []))
        logger.info(f"📊 Raw API returned {elements_count} elements")
    except ValueError:
        logger.error("❌ JSON Decode Error")
        logger.debug(f"Status: {response.status_code}, Response: {response.text[:300]}")
        return []

    # Parse kết quả với tối ưu hóa
    parse_start = time.time()
    places = []
    user_location = (lat, lon)
    skipped_no_name = 0
    skipped_too_far = 0
    
    for element in data.get('elements', []):
        tags = element.get('tags', {})
        name = tags.get('name', 'Unknown')
        
        # Bỏ qua các địa điểm không có tên hoặc tên là Unknown
        if name == 'Unknown' or not name.strip():
            skipped_no_name += 1
            continue

        # Lấy tọa độ
        if 'lat' in element and 'lon' in element:
            el_lat, el_lon = element['lat'], element['lon']
        elif 'center' in element:
            el_lat, el_lon = element['center']['lat'], element['center']['lon']
        else:
            continue

        # Tối ưu: tính distance một lần
        place_location = (el_lat, el_lon)
        distance = geodesic(user_location, place_location).meters
        
        # Bỏ qua nếu quá xa (ngoài radius)
        if distance > radius:
            skipped_too_far += 1
            continue

        # Tối ưu: lấy tags một lần
        address_parts = [
            tags.get('addr:housenumber', ''),
            tags.get('addr:street', ''),
            tags.get('addr:city', ''),
            tags.get('addr:postcode', '')
        ]
        address = ", ".join(filter(None, address_parts)) or 'Không có thông tin'
        
        places.append({
            'name': name,
            'lat': el_lat,
            'lon': el_lon,
            'distance': int(distance),
            'opening_hours': tags.get('opening_hours', 'Không có thông tin'),
            'cuisine': tags.get('cuisine', 'Không có thông tin'),
            'phone': tags.get('phone', 'Không có thông tin'),
            'website': tags.get('website', 'Không có thông tin'),
            'email': tags.get('email', 'Không có thông tin'),
            'address': address,
            'amenity': tags.get('amenity', 'restaurant')
        })
    
    parse_elapsed = (time.time() - parse_start) * 1000
    logger.info(f"🔍 Parsing completed in {parse_elapsed:.1f}ms - Valid: {len(places)}, Skipped (no name): {skipped_no_name}, Skipped (too far): {skipped_too_far}")

    # Sắp xếp theo khoảng cách - gần nhất lên đầu
    sort_start = time.time()
    places.sort(key=lambda x: x['distance'])
    sort_elapsed = (time.time() - sort_start) * 1000
    
    # Giới hạn số lượng kết quả
    places = places[:limit]
    
    # Lưu vào cache
    _last_cache = {
        "timestamp": now,
        "lat": lat,
        "lon": lon,
        "radius": radius,
        "data": places
    }

    total_elapsed = (time.time() - start_time) * 1000
    _performance_stats["avg_response_time"] = (
        (_performance_stats["avg_response_time"] * (_performance_stats["total_requests"] - 1) + total_elapsed) 
        / _performance_stats["total_requests"]
    )
    
    logger.info(f"✅ Returned {len(places)}/{limit} places (sort: {sort_elapsed:.1f}ms)")
    logger.info(f"⏱️ Total request time: {total_elapsed:.1f}ms (avg: {_performance_stats['avg_response_time']:.1f}ms)")
    logger.info(f"📊 Cache efficiency: {_performance_stats['cache_hits']}/{_performance_stats['total_requests']} ({_performance_stats['cache_hits']/_performance_stats['total_requests']*100:.1f}%)")
    logger.info("=" * 70)
    
    return places

# function to find nearest and most info places - tối ưu hóa
def get_recommendations(places):
    if not places:
        return None, None

    # Gần nhất (đã được sort nên có thể lấy đầu tiên)
    nearest = places[0] if places else None

    # Thông tin nhiều nhất với scoring tối ưu
    def info_score(place):
        score = 0
        if place["opening_hours"] != "Không có thông tin":
            score += 2  # Giờ mở cửa quan trọng hơn
        if place["cuisine"] != "Không có thông tin":
            score += 2
        if place["phone"] != "Không có thông tin":
            score += 1.5
        if place["website"] != "Không có thông tin":
            score += 1
        if place["email"] != "Không có thông tin":
            score += 0.5
        if place["address"] != "Không có thông tin":
            score += 1.5
        if place["name"] != "Unknown":
            score += 1
        return score
    
    most_info = max(places, key=info_score) if places else None

    return nearest, most_info

# Function to create a folium map - tối ưu hóa
def create_map(lat, lon, places):
    """Tạo bản đồ với số lượng markers giới hạn để tăng hiệu suất."""
    m = folium.Map(
        location=[lat, lon], 
        zoom_start=14,  # Zoom phù hợp với bán kính 2-3km
        tiles='OpenStreetMap',
        prefer_canvas=True  # Tăng hiệu suất render
    )
    
    # Vị trí người dùng
    folium.CircleMarker(
        location=[lat, lon],
        radius=10,
        color='blue',
        fill=True,
        fill_color='blue',
        fill_opacity=0.7,
        popup='<b>Vị trí của bạn</b>'
    ).add_to(m)

    # Chỉ hiển thị markers cho các quán trong danh sách (đã giới hạn)
    for idx, p in enumerate(places[:30]):  # Tối đa 30 markers trên map
        popup_html = f"""<div style='min-width:150px'>
            <b>{p['name']}</b><br>
            📍 {p['distance']} m<br>
            🍴 {p['cuisine']}
        </div>"""
        
        folium.Marker(
            [p['lat'], p['lon']],
            popup=folium.Popup(popup_html, max_width=200),
            tooltip=f"{idx+1}. {p['name']}",  # Tooltip ngắn gọn
            icon=folium.Icon(color='red', icon='cutlery', prefix='fa')
        ).add_to(m)

    return m._repr_html_()

@app.route("/map")
def map_view():
    request_start = time.time()
    lat = float(request.args.get('lat', 21.028511))
    lon = float(request.args.get('lon', 105.804817))
    radius = int(request.args.get('radius', 2000))  # Bán kính mặc định 2km
    limit = int(request.args.get('limit', 30))      # Giới hạn 30 quán
    
    # Giới hạn tham số để tránh quá tải
    radius = min(radius, 5000)  # Tối đa 5km
    limit = min(limit, 50)      # Tối đa 50 quán
    
    logger.info(f"\n{'='*70}")
    logger.info(f"🌐 New /map request from {request.remote_addr}")
    
    places = get_nearby_places(lat, lon, radius, limit)
    
    rec_start = time.time()
    nearest, most_info = get_recommendations(places)
    rec_elapsed = (time.time() - rec_start) * 1000
    logger.info(f"⭐ Recommendations computed in {rec_elapsed:.1f}ms")
    
    map_start = time.time()
    map_html = create_map(lat, lon, places)
    map_elapsed = (time.time() - map_start) * 1000
    logger.info(f"🗺️ Map generated in {map_elapsed:.1f}ms")
    
    total_request_time = (time.time() - request_start) * 1000
    logger.info(f"✅ Total /map request time: {total_request_time:.1f}ms")
    
    return render_template('index.html', map_html=map_html, places=places,nearest=nearest,
        most_info=most_info, radius=radius, limit=limit)

@app.route("/api/chat", methods=['POST'])
def chat():
    chat_start = time.time()
    data = request.json
    user_message = data.get('message', '')
    places = data.get('places', [])
    
    logger.info(f"\n{'='*70}")
    logger.info(f"💬 Chat request: '{user_message[:50]}...' ({len(places)} places context)")
    
    if not user_message:
        return jsonify({'error': 'Message is required'}), 400
    
    if not model or not api_key:
        logger.error("❌ Gemini API not configured")
        return jsonify({'error': 'Gemini API key not configured. Please add GEMINI_API_KEY to .env file'}), 500
    
    # Create context from nearby places (from Overpass API)
    places_context = "\n".join([f"- {p['name']}: {p['distance']}m away, {p['cuisine']}, {p['address']}" for p in places[:5]]) if places else "No nearby places found"
    
    # Create context from datasheet.csv
    datasheet_context = ""
    if restaurant_data:
        datasheet_context = "## Restaurant Database from Datasheet:\n"
        datasheet_context += "\n".join([
            f"- {row.get('name', 'N/A')}: {', '.join([f'{k}={v}' for k, v in row.items() if k != 'name'])}"
            for row in restaurant_data[:15]
        ])
    else:
        datasheet_context = "No restaurant database available"
    
    system_prompt = f"""You are a helpful restaurant recommendation assistant with expertise in Vietnamese cuisine and dining.

## Nearby Places Found (from OpenStreetMap):
{places_context}

{datasheet_context}

## Your Role:
1. Analyze user preferences and recommend the best restaurants
2. Combine information from nearby places and the restaurant database
3. Consider cuisine type, price range, rating, and opening hours
4. Provide practical information like phone numbers and addresses
5. Respond in Vietnamese with detailed and personalized recommendations
6. If a recommendation matches data from the datasheet, prioritize it
7. Be helpful and suggest alternatives if needed"""
    
    try:
        full_message = system_prompt + "\n\nUser Request: " + user_message
        
        gemini_start = time.time()
        response = model.generate_content(full_message)
        gemini_elapsed = (time.time() - gemini_start) * 1000
        
        assistant_message = response.text
        total_chat_time = (time.time() - chat_start) * 1000
        
        logger.info(f"✅ Gemini response received in {gemini_elapsed:.1f}ms (total: {total_chat_time:.1f}ms)")
        logger.info(f"📝 Response length: {len(assistant_message)} chars")
        
        return jsonify({'reply': assistant_message})
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Gemini API error: {error_msg}")
        
        # Handle specific error cases
        if 'quota' in error_msg.lower() or 'rate_limit' in error_msg.lower():
            return jsonify({'error': 'Gemini quota exceeded or rate limited. Please try again later'}), 429
        elif '401' in error_msg or 'authentication' in error_msg.lower() or 'invalid' in error_msg.lower():
            return jsonify({'error': 'Invalid Gemini API key. Please check your .env file'}), 401
        else:
            return jsonify({'error': f'Error: {error_msg}'}), 500

def open_browser():
    webbrowser.open("http://127.0.0.1:5000/map")

if __name__ == "__main__":
    logger.info("\n" + "="*70)
    logger.info("🚀 Starting NearBuyFood Application")
    logger.info("="*70)
    logger.info(f"📋 Restaurant data loaded: {len(restaurant_data)} entries")
    logger.info(f"🤖 Gemini API configured: {'Yes' if model else 'No'}")
    logger.info(f"🌎 Server will start at: http://127.0.0.1:5000/map")
    logger.info("="*70 + "\n")
    
    threading.Timer(1, open_browser).start()
    app.run(debug=False)
