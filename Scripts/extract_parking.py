import geopandas as gd

print("Loading Gießen.geojson...")
gdf = gd.read_file("data/testing/Gießen.geojson", engine="pyogrio")

print("Filtering for parking features...")
parking = gdf[gdf['amenity'] == 'parking']

# Keep only relevant columns if they exist
columns_to_keep = ['geometry', 'amenity', 'parking', 'name']
available_columns = [col for col in columns_to_keep if col in parking.columns]

parking_clean = parking[available_columns].dropna(subset=['geometry'])

output_path = "data/testing/giessen_parking.geojson"
parking_clean.to_file(output_path, driver="GeoJSON")
print(f"Saved {len(parking_clean)} parking features to {output_path}!")
