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

load_dotenv()
app = Flask(__name__)


# ==============================
# 1. GOOGLE SHEET CONFIG
# ==============================
SHEET_ID = "1inrbMAXd3CXE0kK8QA_tFY8kIhU7V1L8ZwrgWAqndzY"   # <<< Thay bằng ID Google Sheet của bạn
CREDS_FILE = "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Cache chống spam API khi reload quá nhanh
_last_cache = {
    "timestamp": 0,
    "lat": None,
    "lon": None,
    "radius": None,
    "data": None
}
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

# Load restaurant data from CSV
def load_restaurants_from_csv(csv_file='datasheet.csv'):
    restaurants = []
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                restaurants.append(row)
        print(f"Loaded {len(restaurants)} restaurants from {csv_file}")
    except FileNotFoundError:
        print(f"Warning: {csv_file} not found")
    return restaurants

restaurant_data = load_restaurants_from_csv()

# ==============================
#functions to get nearby places from OpenStreetMap
def get_nearby_places(lat, lon, radius=3000):
    global _last_cache

    # Cache 30 giây — tránh crash khi Ctrl+S / load lại liên tục
    now = time.time()
    if (_last_cache["data"] is not None 
        and now - _last_cache["timestamp"] <= 30
        and _last_cache["lat"] == lat
        and _last_cache["lon"] == lon
        and _last_cache["radius"] == radius):

        print("⚠️ Using cached result to avoid API spam")
        return _last_cache["data"]

    overpass_url = "http://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json];
    (
      node["amenity"="restaurant"](around:{radius},{lat},{lon});
      node["amenity"="cafe"](around:{radius},{lat},{lon});
      node["amenity"="bar"](around:{radius},{lat},{lon});
    );
    out center;
    """

    try:
        response = requests.get(
            overpass_url,
            params={'data': overpass_query},
            timeout=10   # tránh treo
        )
    except Exception as e:
        print("❌ Request failed:", e)
        return []

    # Không phải status 200 → API lỗi
    if response.status_code != 200:
        print("❌ Overpass API Error:", response.status_code)
        print("RAW:", response.text[:300])
        return []

    # Thử parse JSON
    try:
        data = response.json()
    except ValueError:
        print("❌ JSON Decode Error")
        print("Status:", response.status_code)
        print("RAW:", response.text[:300])
        return []

    # Parse kết quả
    places = []
    for element in data.get('elements', []):
        name = element.get('tags', {}).get('name', 'Unknown')

        # Lấy tọa độ
        if 'lat' in element and 'lon' in element:
            el_lat, el_lon = element['lat'], element['lon']
        elif 'center' in element:
            el_lat, el_lon = element['center']['lat'], element['center']['lon']
        else:
            continue

        distance = geodesic((lat, lon), (el_lat, el_lon)).meters

        places.append({
            'name': name,
            'lat': el_lat,
            'lon': el_lon,
            'distance': int(distance),
            'opening_hours': element.get('tags', {}).get('opening_hours', 'Không có thông tin'),
            'cuisine': element.get('tags', {}).get('cuisine', 'Không có thông tin'),
            'phone': element.get('tags', {}).get('phone', 'Không có thông tin'),
            'website': element.get('tags', {}).get('website', 'Không có thông tin'),
            'email': element.get('tags', {}).get('email', 'Không có thông tin'),
            'address': ", ".join(filter(None, [
                element.get('tags', {}).get('addr:housenumber'),
                element.get('tags', {}).get('addr:street'),
                element.get('tags', {}).get('addr:city'),
                element.get('tags', {}).get('addr:postcode')
            ])) or 'Không có thông tin',
        })

    # Lưu vào cache
    _last_cache = {
        "timestamp": now,
        "lat": lat,
        "lon": lon,
        "radius": radius,
        "data": places
    }

    return places

# function to find nearest and most info places
def get_recommendations(places):
    if not places:
        return None, None

    # Gần nhất
    nearest = min(places, key=lambda x: x["distance"])

    # Thông tin nhiều nhất
    most_info = max(places, key=lambda x: sum([
        x["opening_hours"] != "Không có thông tin",
        x["cuisine"] != "Không có thông tin",
        x["phone"] != "Không có thông tin",
        x["website"] != "Không có thông tin",
        x["email"] != "Không có thông tin",
        x["address"] != "Không có thông tin",
    ]))

    return nearest, most_info

# Function to create a folium map
def create_map(lat, lon, places):
    m = folium.Map(location=[lat, lon], zoom_start=15)
    folium.CircleMarker(
        location=[lat, lon],
        radius=10,
        color='blue',
        fill=True,
        fill_color='blue',
        fill_opacity=0.7,
        popup='Vị trí của bạn'
    ).add_to(m)

    for p in places:
        popup_text = f"{p['name']} - {p['distance']} m"
        folium.Marker(
            [p['lat'], p['lon']],
            popup=popup_text,
            icon=folium.Icon(color='red', icon='cutlery', prefix='fa')
        ).add_to(m)

    return m._repr_html_()

@app.route("/map")
def map_view():
    lat = float(request.args.get('lat', 21.028511))
    lon = float(request.args.get('lon', 105.804817))
    places = get_nearby_places(lat, lon)
    # change
    nearest, most_info = get_recommendations(places)
      # Ghi Google Sheets
    if places:
        write_to_sheet(places)
    # change end
    map_html = create_map(lat, lon, places)
    return render_template('index.html', map_html=map_html, places=places,nearest=nearest,
        most_info=most_info)

@app.route("/api/chat", methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message', '')
    places = data.get('places', [])
    
    if not user_message:
        return jsonify({'error': 'Message is required'}), 400
    
    if not model or not api_key:
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
        response = model.generate_content(full_message)
        
        assistant_message = response.text
        return jsonify({'reply': assistant_message})
    
    except Exception as e:
        error_msg = str(e)
        print(f"Error calling Gemini API: {error_msg}")
        
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
    threading.Timer(1, open_browser).start()
    app.run(debug=False)
