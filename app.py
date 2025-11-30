from flask import Flask, render_template, request, jsonify
import folium
import requests
import threading
import webbrowser
from geopy.distance import geodesic
import random
import os
import csv
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

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

def get_nearby_places(lat, lon, radius=2000):
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
        response = requests.get(overpass_url, params={'data': overpass_query}, timeout=10)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        print(f"Error fetching nearby places: {e}")
        return []

    places = []
    for element in data.get('elements', []):
        name = element.get('tags', {}).get('name', 'Unknown')
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
    return places

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
    map_html = create_map(lat, lon, places)
    return render_template('index.html', map_html=map_html, places=places)

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
