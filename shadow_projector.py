import math
import shapely.affinity
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
import geopandas as gp

def project_building_shadow(geometry, height, altitude_deg, azimuth_deg):
    """
    Projects the shadow of a building footprint.
    Parameters:
      geometry: shapely Polygon or MultiPolygon (in projected coordinate system, e.g. EPSG:25832)
      height: building height in meters
      altitude_deg: sun altitude in degrees (0 = horizon, 90 = zenith)
      azimuth_deg: sun azimuth in degrees (0 = North, 90 = East, etc.)
    Returns:
      A shapely Polygon or MultiPolygon representing the shadow cast by the building.
    """
    if geometry is None or geometry.is_empty:
        return geometry

    # If geometry is invalid, try to make it valid
    if not geometry.is_valid:
        try:
            geometry = geometry.buffer(0)
        except Exception:
            pass

    if altitude_deg <= 2.0:
        # Sun is extremely low or below the horizon, shadows are either infinite or non-existent.
        # We clamp to 2 degrees to avoid division by zero or extremely long shadows.
        altitude_deg = 2.0

    # L = height / tan(altitude)
    rad_alt = math.radians(altitude_deg)
    rad_az = math.radians(azimuth_deg)
    
    # Calculate shadow length and clamp to a maximum of 100 meters
    shadow_len = height / math.tan(rad_alt)
    shadow_len = min(shadow_len, 100.0)
    
    # Project away from the sun
    dx = -shadow_len * math.sin(rad_az)
    dy = -shadow_len * math.cos(rad_az)
    
    try:
        if isinstance(geometry, Polygon):
            translated = shapely.affinity.translate(geometry, xoff=dx, yoff=dy)
            # Convex hull of the union of original and translated geometry represents the shadow volume
            return unary_union([geometry, translated]).convex_hull
        elif isinstance(geometry, MultiPolygon):
            shadows = []
            for poly in geometry.geoms:
                if not poly.is_valid:
                    try:
                        poly = poly.buffer(0)
                    except Exception:
                        pass
                translated = shapely.affinity.translate(poly, xoff=dx, yoff=dy)
                shadows.append(unary_union([poly, translated]).convex_hull)
            return unary_union(shadows)
        else:
            return geometry
    except Exception as e:
        print(f"Error projecting shadow for building geometry: {e}")
        try:
            # Fallback: try taking convex hull first, then translating
            hull = geometry.convex_hull
            translated = shapely.affinity.translate(hull, xoff=dx, yoff=dy)
            return unary_union([hull, translated]).convex_hull
        except Exception:
            return geometry

def get_shadows_gdf(buildings_gdf, altitude_deg, azimuth_deg):
    """
    Projects shadows for a GeoDataFrame of buildings.
    """
    if altitude_deg <= 0:
        # Night time, return an empty GeoDataFrame
        return gp.GeoDataFrame(geometry=[], crs=buildings_gdf.crs)
        
    shadow_geometries = []
    for _, row in buildings_gdf.iterrows():
        # Try explicit 'height' tag
        height_raw = row.get('height')
        height = None
        if height_raw is not None:
            try:
                h_val = float(height_raw)
                if not math.isnan(h_val) and not math.isinf(h_val):
                    height = h_val
            except (ValueError, TypeError):
                pass
                
        if height is None:
            # Determine building type and levels
            b_type = row.get('building')
            if b_type is None or (isinstance(b_type, float) and math.isnan(b_type)):
                b_type = 'yes'
            b_type = str(b_type).lower()
            
            levels_raw = row.get('building:levels')
            has_levels = False
            levels_val = 0.0
            if levels_raw is not None:
                try:
                    l_val = float(levels_raw)
                    if not math.isnan(l_val) and not math.isinf(l_val):
                        levels_val = l_val
                        has_levels = True
                except (ValueError, TypeError):
                    pass
            
            # Category-specific level heights and default heights
            if b_type in ['commercial', 'industrial', 'retail', 'office', 'warehouse', 'supermarket', 'manufactory', 'work']:
                level_height = 3.8
                default_height = 15.0
            elif b_type in ['public', 'civic', 'government', 'hospital', 'school', 'university', 'kindergarten', 'museum', 'church', 'cathedral', 'monastery']:
                level_height = 4.0
                default_height = 25.0 if b_type in ['church', 'cathedral'] else 15.0
            elif b_type in ['apartments', 'residential', 'dormitory']:
                level_height = 3.0
                default_height = 15.0
            elif b_type in ['house', 'detached', 'semi-detached', 'terrace', 'bungalow', 'cabin']:
                level_height = 2.8
                default_height = 8.0
            elif b_type in ['garage', 'garages', 'carport', 'shed', 'greenhouse', 'ruins', 'boathouse']:
                level_height = 2.5
                default_height = 3.0
            else:
                level_height = 3.0
                default_height = 10.0
                
            if has_levels:
                height = levels_val * level_height
            else:
                height = default_height
                
        shadow_poly = project_building_shadow(row['geometry'], height, altitude_deg, azimuth_deg)
        shadow_geometries.append(shadow_poly)
        
    shadows_gdf = buildings_gdf.copy()
    shadows_gdf['geometry'] = shadow_geometries
    return shadows_gdf
