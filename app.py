import os
import datetime
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dateutil.parser import parse as parse_date
import geopandas as gp
from shapely.geometry import Polygon
from pyproj import Transformer
import shadow_projector
from router import ThermoRouter
import uvicorn
from pysolar.solar import get_altitude, get_azimuth
import weather_service

app = FastAPI(title="ThermoRoute API", description="Micro-Climate Routing for Gießen")

# Initialize router globally
print("Initializing ThermoRouter. This may take up to a minute to load and project data...")
router = ThermoRouter(
    walk_network_path="data/testing/giessen_walk_network.geojson",
    buildings_path="data/testing/giessen_buildings.geojson",
    microclimate_path="data/testing/giessen_microclimate.geojson",
    parking_path="data/testing/giessen_parking.geojson"
)
print("ThermoRouter loaded successfully!")

class RouteRequest(BaseModel):
    start_lon: float
    start_lat: float
    end_lon: float
    end_lat: float
    beta: float
    time: str

def get_solar_angles(lat: float, lon: float, dt_utc: datetime.datetime):
    """
    Computes sun altitude and azimuth using pysolar.
    """
    # pysolar expects latitude, longitude, and a timezone-aware datetime object
    try:
        altitude = get_altitude(lat, lon, dt_utc)
        azimuth = get_azimuth(lat, lon, dt_utc)
        # Ensure altitude is not negative (below horizon)
        altitude = max(0.0, altitude)
        return altitude, azimuth
    except Exception as e:
        print(f"Error calculating solar angles: {e}")
        # Summer solstice midday fallback for Gießen
        return 60.0, 180.0

@app.get("/api/weather")
async def get_weather(lat: float, lon: float, time: str):
    """
    Returns the temperature and comfort description for the given coordinates and time.
    """
    try:
        dt = parse_date(time)
        dt_utc = dt.astimezone(datetime.timezone.utc)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid time format: {e}")
        
    temp = weather_service.get_temperature(lat, lon, dt_utc)
    
    # Generate simple semantic description based on temperature
    if temp >= 32.0:
        desc = "Scorching Heat"
    elif temp >= 26.0:
        desc = "Hot & Sunny"
    elif temp >= 20.0:
        desc = "Warm & Sunny"
    elif temp >= 15.0:
        desc = "Mild / Pleasant"
    elif temp >= 8.0:
        desc = "Chilly"
    else:
        desc = "Cold"
        
    return {
        "temperature": temp,
        "description": desc
    }

@app.get("/api/shadows")
async def get_shadows(time: str, min_lon: float, min_lat: float, max_lon: float, max_lat: float):
    """
    Returns the projected building shadows in EPSG:4326 for a given bounding box and time.
    """
    try:
        dt = parse_date(time)
        dt_utc = dt.astimezone(datetime.timezone.utc)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid time format: {e}")
        
    # Calculate solar angles for the midpoint of the bounding box
    mid_lat = (min_lat + max_lat) / 2.0
    mid_lon = (min_lon + max_lon) / 2.0
    altitude, azimuth = get_solar_angles(mid_lat, mid_lon, dt_utc)
    
    if altitude <= 0:
        return {"type": "FeatureCollection", "features": []}
        
    # Query buildings in the bounding box
    # 1. Transform bbox to UTM
    transformer_to_metric = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    transformer_to_wgs84 = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    
    x1, y1 = transformer_to_metric.transform(min_lon, min_lat)
    x2, y2 = transformer_to_metric.transform(max_lon, max_lat)
    bbox_poly = Polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2)])
    
    # Query building spatial index
    building_indices = router.buildings_tree.query(bbox_poly)
    if len(building_indices) == 0:
        return {"type": "FeatureCollection", "features": []}
        
    filtered_buildings = router.buildings_gdf.iloc[building_indices]
    
    # Project shadows in UTM
    shadows_gdf = shadow_projector.get_shadows_gdf(filtered_buildings, altitude, azimuth)
    
    # Convert shadows back to EPSG:4326
    shadows_wgs = shadows_gdf.to_crs("EPSG:4326")
    
    # Format as GeoJSON
    return shadows_wgs.__geo_interface__

@app.post("/api/route")
async def get_route(req: RouteRequest):
    """
    Computes the shadiest walking path from start to end points.
    """
    try:
        dt = parse_date(req.time)
        dt_utc = dt.astimezone(datetime.timezone.utc)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid time format: {e}")
        
    # Calculate solar angles at the route start
    altitude, azimuth = get_solar_angles(req.start_lat, req.start_lon, dt_utc)
    
    # Fetch ambient temperature at start location
    temperature = weather_service.get_temperature(req.start_lat, req.start_lon, dt_utc)
    
    # Run the router
    route_geojson = router.get_route(
        start_lon=req.start_lon,
        start_lat=req.start_lat,
        end_lon=req.end_lon,
        end_lat=req.end_lat,
        beta=req.beta,
        altitude=altitude,
        azimuth=azimuth,
        temperature=temperature
    )
    
    if route_geojson is None:
        raise HTTPException(status_code=404, detail="No route found between selected points.")
        
    return route_geojson

@app.get("/api/parking")
async def get_parking(dest_lon: float, dest_lat: float, time: str):
    """
    Ranks nearby parking lots by heat/shade level for the destination.
    """
    try:
        dt = parse_date(time)
        dt_utc = dt.astimezone(datetime.timezone.utc)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid time format: {e}")
        
    altitude, azimuth = get_solar_angles(dest_lat, dest_lon, dt_utc)
    
    # Fetch ambient temperature at destination
    temperature = weather_service.get_temperature(dest_lat, dest_lon, dt_utc)
    
    parking_geojson = router.get_cool_parking(dest_lon, dest_lat, altitude, azimuth, temperature=temperature)
    return parking_geojson

# Serve index.html directly from root URL
@app.get("/")
async def get_index():
    return FileResponse("static/index.html")

# Serve static files (HTML, CSS, JS)
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Mount data/testing directory for static file access (e.g. for microclimate rendering)
app.mount("/data", StaticFiles(directory="data/testing"), name="data")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
