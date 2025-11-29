from flask import Flask, render_template, request, jsonify
import folium
import requests
import threading
import webbrowser
from geopy.distance import geodesic
import random
import csv
import os
from collections import defaultdict

app = Flask(__name__)

# Đường dẫn file CSV
CSV_FILE = os.path.join(os.path.dirname(__file__), 'sheet.csv')

def read_csv_data():
    """Đọc dữ liệu từ sheet.csv"""
    try:
        data = []
        with open(CSV_FILE, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                data.append(row)
        return data
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return []

def analyze_csv_data(data):
    """Phân tích dữ liệu từ CSV"""
    if not data:
        return {}
    
    # Chuyển đổi dữ liệu
    places_by_type = defaultdict(list)
    places_by_category = defaultdict(list)
    all_ratings = []
    
    for place in data:
        try:
            rating = float(place.get('rating', 0))
            all_ratings.append(rating)
            places_by_type[place.get('type', 'Unknown')].append(place)
            places_by_category[place.get('category', 'Unknown')].append(place)
        except:
            continue
    
    # Tìm các điểm đặc biệt
    best_rated = max(data, key=lambda x: float(x.get('rating', 0)), default=None)
    cheapest = min(data, key=lambda x: int(x.get('price_level', 0)), default=None)
    quietest = [p for p in data if p.get('noise_level', '').lower() == 'thấp']
    best_for_work = [p for p in data if p.get('category', '').lower() in ['cafe học bài', 'ăn trưa']]
    
    analysis = {
        'total_places': len(data),
        'avg_rating': sum(all_ratings) / len(all_ratings) if all_ratings else 0,
        'best_rated': best_rated,
        'cheapest': cheapest,
        'quietest_places': quietest[:3],
        'best_for_work': best_for_work,
        'types': list(places_by_type.keys()),
        'categories': list(places_by_category.keys()),
        'type_count': {k: len(v) for k, v in places_by_type.items()},
        'category_count': {k: len(v) for k, v in places_by_category.items()},
        'wifi_available': len([p for p in data if p.get('wifi', '').lower() == 'có']),
        'power_sockets': len([p for p in data if p.get('power_sockets', '').lower() in ['nhiều', 'vừa', 'có']]),
    }
    
    return analysis

def process_chat_intent(message, data, analysis):
    """Xử lý ý định chat và trả về insights"""
    message_lower = message.lower()
    
    # 🌟 Nhận định cao nhất
    if any(word in message_lower for word in ['rating cao', 'đánh giá cao', 'tốt nhất', 'hay nhất', 'ngon nhất']):
        if analysis.get('best_rated'):
            place = analysis['best_rated']
            return f"""⭐ **{place['name']}** được đánh giá cao nhất!
• Rating: {place['rating']}/5 ⭐
• Loại: {place['type']} - {place['category']}
• Giá: {place['price_desc']}
• Giờ mở: {place['opening_hours']}
• 💬 {place['note']}"""
    
    # 💰 Nhận định giá rẻ
    if any(word in message_lower for word in ['giá rẻ', 'rẻ nhất', 'bình dân', 'giá tốt', 'giá hợp lý']):
        if analysis.get('cheapest'):
            place = analysis['cheapest']
            return f"""💰 **{place['name']}** là quán rẻ nhất!
• Mức giá: {place['price_desc']}
• Rating: {place['rating']}/5 ⭐
• Loại: {place['type']}
• Giờ mở: {place['opening_hours']}
• 💬 {place['note']}"""
    
    # 🤫 Nhận định yên tĩnh
    if any(word in message_lower for word in ['yên tĩnh', 'im lặng', 'yên', 'xích lô', 'ồn ào']):
        if analysis.get('quietest_places'):
            response = "🤫 **Những nơi yên tĩnh nhất**:\n"
            for place in analysis['quietest_places']:
                response += f"\n• **{place['name']}** ({place['rating']}/5)\n"
                response += f"  - {place['price_desc']} | {place['note']}"
            return response
    
    # 💼 Nhận định để làm việc/học bài
    if any(word in message_lower for word in ['học bài', 'làm việc', 'code', 'wifi', 'socket', 'điện', 'làm việc']):
        response = "💼 **Top nơi để học bài / làm việc**:\n"
        for place in analysis.get('best_for_work', [])[:4]:
            wifi_status = "✅" if place.get('wifi', '').lower() == 'có' else "❌"
            socket_status = place.get('power_sockets', 'Không rõ')
            response += f"\n• **{place['name']}** ({place['rating']}/5)\n"
            response += f"  - WiFi: {wifi_status} | Ổ cắm: {socket_status}\n"
            response += f"  - {place['price_desc']} | Vùng: {place['noise_level']}\n"
            response += f"  - 💬 {place['note']}"
        return response
    
    # 📊 Thống kê tổng thể
    if any(word in message_lower for word in ['thống kê', 'tổng', 'overview', 'bao nhiêu', 'có mấy']):
        response = f"""📊 **THỐNG KÊ TỔNG QUÁT**

🏪 Tổng số địa điểm: **{analysis['total_places']}**
⭐ Rating trung bình: **{analysis['avg_rating']:.1f}/5**

📍 **Phân loại**:
"""
        for place_type, count in analysis['type_count'].items():
            response += f"• {place_type}: {count}\n"
        
        response += "\n🏷️ **Danh mục**:\n"
        for category, count in analysis['category_count'].items():
            response += f"• {category}: {count}\n"
        
        response += f"""
🌐 WiFi: **{analysis['wifi_available']}** chỗ có
🔌 Ổ cắm điện: **{analysis['power_sockets']}** chỗ tốt"""
        return response
    
    # 🍽️ Nhận định theo loại
    if any(word in message_lower for word in ['café', 'cafe', 'coffee', 'nhà hàng', 'restaurant', 'quán nhậu', 'bar']):
        query_type = 'cafe' if any(w in message_lower for w in ['café', 'cafe', 'coffee']) else 'restaurant' if 'nhà hàng' in message_lower else 'bar'
        places = [p for p in data if query_type in p.get('type', '').lower()]
        
        if places:
            response = f"🏪 **{len(places)} {query_type.upper()} gần đây**:\n"
            for i, place in enumerate(places[:5], 1):
                response += f"\n{i}. **{place['name']}** ({place['rating']}/5 ⭐)\n"
                response += f"   • {place['price_desc']}\n"
                response += f"   • Giờ mở: {place['opening_hours']}\n"
                response += f"   • {place['note']}"
            return response
    
    # 🔥 Nhận định phổ biến/tối thích
    if any(word in message_lower for word in ['phổ biến', 'nổi tiếng', 'hot', 'review', 'nước ngoài']):
        places = sorted(data, key=lambda x: float(x.get('rating', 0)), reverse=True)[:3]
        response = "🔥 **Top 3 địa điểm được yêu thích nhất**:\n"
        for i, place in enumerate(places, 1):
            response += f"\n{i}. **{place['name']}** - {place['rating']}/5 ⭐\n"
            response += f"   • {place['category']}\n"
            response += f"   • {place['note']}"
        return response
    
    # Mặc định - gợi ý hỏi
    return """👋 **Xin chào! Tôi là NearBuyFood Assistant**

📊 Tôi có thể giúp bạn:
• ⭐ Tìm quán được đánh giá cao nhất
• 💰 Tìm quán giá rẻ nhất
• 🤫 Tìm nơi yên tĩnh để học bài/làm việc
• 💼 Địa điểm tốt cho làm việc (WiFi, ổ cắm)
• 📊 Xem thống kê tổng quát
• 🍽️ Danh sách café, nhà hàng, quán nhậu
• 🔥 Top địa điểm nổi tiếng

**Hỏi tôi gì đi!** 😊"""

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
    
    # Lấy dữ liệu từ CSV
    csv_data = read_csv_data()
    csv_analysis = analyze_csv_data(csv_data)
    
    return render_template('index.html', map_html=map_html, places=places, 
                         csv_data=csv_data, csv_analysis=csv_analysis)

@app.route("/api/chat", methods=["POST"])
def chat():
    """Chatbot endpoint - phân tích dữ liệu CSV và trả lời"""
    try:
        user_message = request.json.get("message", "").strip()
        
        if not user_message:
            return jsonify({
                "status": "error",
                "response": "Vui lòng nhập câu hỏi!"
            })
        
        # Lấy dữ liệu từ CSV
        csv_data = read_csv_data()
        analysis = analyze_csv_data(csv_data)
        
        # Phân tích ý định người dùng
        response = process_chat_intent(user_message, csv_data, analysis)
        
        return jsonify({
            "status": "success",
            "response": response
        })
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({
            "status": "error",
            "response": f"❌ Lỗi: {str(e)}"
        })

def open_browser():
    webbrowser.open("http://127.0.0.1:5000/map")

if __name__ == "__main__":
    threading.Timer(1, open_browser).start()
    app.run(debug=False)
