from flask import Flask, render_template, request
import folium
import requests
import threading
import webbrowser
from geopy.distance import geodesic  # để tính khoảng cách

app = Flask(__name__)

def create_map(lat, lon, radius=1500):
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

    m = folium.Map(location=[lat, lon], zoom_start=15)

    # ⭐ Marker vị trí người dùng
    folium.CircleMarker(
        location=[lat, lon],
        radius=10,
        color='blue',
        fill=True,
        fill_color='blue',
        fill_opacity=0.7,
        popup='Vị trí của bạn'
    ).add_to(m)

    # ⭐ Markers quán ăn/cafe/bar + khoảng cách
    for element in data.get('elements', []):
        name = element.get('tags', {}).get('name', 'Unknown')
        if 'lat' in element and 'lon' in element:
            el_lat, el_lon = element['lat'], element['lon']
        elif 'center' in element:
            el_lat, el_lon = element['center']['lat'], element['center']['lon']
        else:
            continue

        # Tính khoảng cách từ người dùng
        distance = geodesic((lat, lon), (el_lat, el_lon)).meters
        popup_text = f"{name} - {int(distance)} m"

        folium.Marker(
            [el_lat, el_lon],
            popup=popup_text,
            icon=folium.Icon(color='red', icon='cutlery', prefix='fa')
        ).add_to(m)

    return m._repr_html_()

@app.route("/map")
def map_view():
    lat = float(request.args.get('lat', 21.028511))
    lon = float(request.args.get('lon', 105.804817))
    map_html = create_map(lat, lon)
    return render_template('index.html', map_html=map_html, lat=lat, lon=lon)

def open_browser():
    webbrowser.open("http://127.0.0.1:5000/map")

if __name__ == "__main__":
    threading.Timer(1, open_browser).start()
    app.run(debug=True)
