import datetime
import urllib.request
import json
import math

# In-memory cache for weather data
# Keys: (date_str, round(lat, 2), round(lon, 2))
# Values: list of 24 hourly temperatures
WEATHER_CACHE = {}

def get_fallback_temperature(dt_utc: datetime.datetime) -> float:
    """
    Returns a fallback temperature based on the month and hour if the weather API is unavailable.
    """
    # Monthly average temperatures for Gießen, Germany (approximate)
    monthly_averages = {
        1: 1.5,   # Jan
        2: 2.0,   # Feb
        3: 5.5,   # Mar
        4: 9.5,   # Apr
        5: 14.0,  # May
        6: 18.0,  # Jun
        7: 20.0,  # Jul
        8: 19.5,  # Aug
        9: 15.5,  # Sep
        10: 10.5, # Oct
        11: 5.5,  # Nov
        12: 2.5   # Dec
    }
    
    # Base temperature for the month
    base_temp = monthly_averages.get(dt_utc.month, 15.0)
    
    # Local hour approximation (Gießen is UTC+1 in winter, UTC+2 in summer)
    # We estimate UTC+2 for general summer-biased use cases
    local_hour = (dt_utc.hour + 2) % 24
    
    # Cosine wave to model daily temperature cycles: max at 15:00, min at 03:00
    variation = 5.0 * math.cos(math.radians((local_hour - 15) * 15))
    
    return round(base_temp + variation, 1)

def fetch_hourly_temperatures_from_api(lat: float, lon: float, date_str: str, is_forecast: bool):
    """
    Queries the Open-Meteo API for a specific date and returns a list of 24 hourly temperatures.
    """
    if is_forecast:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m&start_date={date_str}&end_date={date_str}&timezone=auto"
    else:
        url = f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&hourly=temperature_2m&start_date={date_str}&end_date={date_str}&timezone=auto"
        
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'ThermoRoute-App/1.0'}
        )
        # 3 seconds timeout keeps the interface responsive
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
            if "hourly" in data and "temperature_2m" in data["hourly"]:
                temps = data["hourly"]["temperature_2m"]
                if len(temps) >= 24:
                    return temps
    except Exception as e:
        print(f"Error fetching weather from API ({url}): {e}")
        
    return None

def get_temperature(lat: float, lon: float, dt_utc: datetime.datetime) -> float:
    """
    Returns the ambient air temperature at a given coordinate and UTC time.
    """
    lat_round = round(lat, 2)
    lon_round = round(lon, 2)
    date_str = dt_utc.strftime("%Y-%m-%d")
    hour = dt_utc.hour
    
    cache_key = (date_str, lat_round, lon_round)
    
    if cache_key in WEATHER_CACHE:
        temps = WEATHER_CACHE[cache_key]
        if temps and 0 <= hour < len(temps):
            return temps[hour]
            
    # Determine if target date falls in forecast or archive range
    try:
        today = datetime.date.today()
    except Exception:
        today = datetime.datetime.now().date()
        
    target_date = dt_utc.date()
    is_forecast = target_date >= today and target_date <= today + datetime.timedelta(days=14)
    
    temps = fetch_hourly_temperatures_from_api(lat_round, lon_round, date_str, is_forecast)
    
    if temps is not None and len(temps) >= 24:
        WEATHER_CACHE[cache_key] = temps
        if 0 <= hour < len(temps):
            return temps[hour]
            
    # Fallback to seasonal estimation if API fails or returns invalid data
    fallback = get_fallback_temperature(dt_utc)
    print(f"Using fallback seasonal temperature for {date_str} {hour:02d}:00 UTC: {fallback}°C")
    return fallback
