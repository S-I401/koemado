"""天気をすぐ答えるためのツール（Open-Meteo。API キー不要）。

天気のような軽い調べものは、外部のエージェントを通さずアバターが直接引く。
"""

import httpx

from aiavatar.sts.llm import Tool

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather code → 日本語
WEATHER_JA = {
    0: "快晴", 1: "晴れ", 2: "晴れ時々曇り", 3: "曇り",
    45: "霧", 48: "霧（着氷）",
    51: "弱い霧雨", 53: "霧雨", 55: "強い霧雨", 56: "着氷性の霧雨", 57: "強い着氷性の霧雨",
    61: "小雨", 63: "雨", 65: "大雨", 66: "着氷性の雨", 67: "強い着氷性の雨",
    71: "小雪", 73: "雪", 75: "大雪", 77: "霧雪",
    80: "にわか雨", 81: "強いにわか雨", 82: "激しいにわか雨",
    85: "にわか雪", 86: "強いにわか雪",
    95: "雷雨", 96: "雹を伴う雷雨", 99: "激しい雹を伴う雷雨",
}
DAY_LABELS = ["今日", "明日", "明後日"]


class WeatherTool:
    def __init__(self, default_location: str = "東京", timeout: float = 10.0):
        self.default_location = default_location
        self.client = httpx.AsyncClient(timeout=timeout)
        self._geo_cache: dict[str, dict] = {}

    async def geocode(self, name: str) -> dict | None:
        if name in self._geo_cache:
            return self._geo_cache[name]
        # 「東京」のように素の名前で引けない地名があるので、接尾辞を付けて再検索する
        for query in (name, name + "市", name + "都", name + "区"):
            resp = await self.client.get(GEOCODE_URL, params={"name": query, "count": 1, "language": "ja", "countryCode": "JP"})
            results = resp.json().get("results") or []
            if results:
                place = results[0]
                self._geo_cache[name] = place
                return place
        return None

    async def get_weather(self, location: str = None, days: int = 2) -> dict:
        location = (location or self.default_location).strip()
        place = await self.geocode(location)
        if not place:
            return {"error": f"「{location}」の場所が見つからなかった。市区町村名で聞き直して。"}

        days = min(3, max(1, int(days or 2)))
        resp = await self.client.get(FORECAST_URL, params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,precipitation",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "Asia/Tokyo",
            "forecast_days": days,
        })
        data = resp.json()
        current = data["current"]
        daily = data["daily"]
        return {
            "place": place["name"] if place.get("admin1") in (None, place["name"]) else f"{place['admin1']}{place['name']}",
            "now": {
                "weather": WEATHER_JA.get(current["weather_code"], "不明"),
                "temperature": current["temperature_2m"],
                "feels_like": current["apparent_temperature"],
            },
            "forecast": [
                {
                    "day": DAY_LABELS[i],
                    "weather": WEATHER_JA.get(daily["weather_code"][i], "不明"),
                    "max": daily["temperature_2m_max"][i],
                    "min": daily["temperature_2m_min"][i],
                    "rain_chance": daily["precipitation_probability_max"][i],
                }
                for i in range(len(daily["time"]))
            ],
        }

    def create_tool(self) -> Tool:
        async def get_weather(location: str = None, days: int = 2):
            try:
                return await self.get_weather(location, days)
            except Exception as ex:
                return {"error": f"天気を取得できなかった: {ex}"}

        return Tool(
            name="get_weather",
            spec={
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": (
                        "日本の地名の現在の天気と、今日から最大3日分の予報（天気・最高/最低気温・降水確率）をすぐに調べる。"
                        f"天気を聞かれたらこれを使う。地名が無ければ{self.default_location}。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "市区町村名（例: 東京、札幌市、大阪）"},
                            "days": {"type": "integer", "minimum": 1, "maximum": 3, "description": "予報の日数。既定 2（今日と明日）"},
                        },
                    },
                },
            },
            func=get_weather,
        )
