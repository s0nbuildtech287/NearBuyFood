from flask import Flask, render_template, request
import folium
import requests
import threading
import webbrowser
from geopy.distance import geodesic
import random

app = Flask(__name__)

# Function to get nearby places using Overpass API
def get_nearby_places(lat, lon, radius=5000):
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
    response = requests.get(overpass_url, params={'data': overpass_query})
    data = response.json()

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
    map_html = create_map(lat, lon, places)
    return render_template('index.html', map_html=map_html, places=places)

def open_browser():
    webbrowser.open("http://127.0.0.1:5000/map")

if __name__ == "__main__":
    threading.Timer(1, open_browser).start()
    app.run(debug=True)
