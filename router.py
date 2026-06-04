import geopandas as gp
import networkx as nx
from shapely.geometry import Point, LineString, Polygon
from shapely import STRtree
from shapely.ops import unary_union
import numpy as np
import math
from pyproj import Transformer
import shadow_projector

class ThermoRouter:
    def __init__(self, walk_network_path, buildings_path, microclimate_path, parking_path):
        print("Loading datasets...")
        self.walk_gdf = gp.read_file(walk_network_path)
        self.buildings_gdf = gp.read_file(buildings_path)
        self.microclimate_gdf = gp.read_file(microclimate_path)
        self.parking_gdf = gp.read_file(parking_path)
        
        # Ensure they are in projected CRS (EPSG:25832) for meters-based calculations
        # Gießen is in UTM Zone 32N (EPSG:25832)
        print("Projecting datasets to EPSG:25832 (UTM Zone 32N)...")
        self.walk_gdf = self.walk_gdf.to_crs("EPSG:25832")
        self.buildings_gdf = self.buildings_gdf.to_crs("EPSG:25832")
        self.microclimate_gdf = self.microclimate_gdf.to_crs("EPSG:25832")
        self.parking_gdf = self.parking_gdf.to_crs("EPSG:25832")
        
        # Build the graph
        print("Building routing graph...")
        self.G = nx.Graph()
        
        for idx, row in self.walk_gdf.iterrows():
            geom = row['geometry']
            if geom is None or not isinstance(geom, LineString):
                continue
            
            coords = list(geom.coords)
            if len(coords) < 2:
                continue
            # Round coordinates to 2 decimal places (1cm resolution in UTM)
            start_node = (round(coords[0][0], 2), round(coords[0][1], 2))
            end_node = (round(coords[-1][0], 2), round(coords[-1][1], 2))
            
            length = geom.length # in meters
            surface = row.get('surface', 'paved')
            if not isinstance(surface, str) or surface is None or (isinstance(surface, float) and math.isnan(surface)):
                surface = 'unknown'
            highway = row.get('highway', 'footway')
            
            self.G.add_edge(start_node, end_node, 
                            length=length, 
                            geometry=geom, 
                            surface=surface, 
                            highway=highway, 
                            index=idx)
            
        # Keep only the largest connected component to ensure connectivity
        if self.G.number_of_nodes() > 0:
            print("Filtering graph to largest connected component...")
            largest_cc = max(nx.connected_components(self.G), key=len)
            self.G = self.G.subgraph(largest_cc).copy()

        print(f"Graph loaded with {self.G.number_of_nodes()} nodes and {self.G.number_of_edges()} edges.")
        
        # Build spatial indexes
        print("Building spatial indexes...")
        self.node_list = list(self.G.nodes())
        self.node_points = [Point(n) for n in self.node_list]
        self.node_tree = STRtree(self.node_points)
        
        self.buildings_tree = STRtree(list(self.buildings_gdf['geometry']))
        self.microclimate_tree = STRtree(list(self.microclimate_gdf['geometry']))
        self.parking_tree = STRtree(list(self.parking_gdf['geometry']))

    def find_nearest_node(self, x, y):
        """
        Finds the nearest node in the graph to the given UTM x, y coordinate.
        """
        p = Point(x, y)
        idx = self.node_tree.nearest(p)
        return self.node_list[idx]

    def get_route(self, start_lon, start_lat, end_lon, end_lat, beta, altitude, azimuth, temperature=22.0):
        """
        Calculates the shadiest path from start to end coordinates based on the comfort factor beta,
        ambient temperature, and the solar position (altitude, azimuth).
        """
        # Transformers for coordinate projection
        transformer_to_metric = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
        transformer_to_wgs84 = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
        
        # Convert start/end to UTM
        start_x, start_y = transformer_to_metric.transform(start_lon, start_lat)
        end_x, end_y = transformer_to_metric.transform(end_lon, end_lat)
        
        source = self.find_nearest_node(start_x, start_y)
        target = self.find_nearest_node(end_x, end_y)
        
        # Define search bounding box expanded by a buffer (e.g. 500 meters)
        buffer_dist = 500.0
        min_x = min(start_x, end_x) - buffer_dist
        max_x = max(start_x, end_x) + buffer_dist
        min_y = min(start_y, end_y) - buffer_dist
        max_y = max(start_y, end_y) + buffer_dist
        
        bbox_poly = Polygon([(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)])
        
        # Filter buildings in bbox
        building_indices = self.buildings_tree.query(bbox_poly)
        filtered_buildings = self.buildings_gdf.iloc[building_indices]
        
        # Generate building shadows
        shadows_gdf = shadow_projector.get_shadows_gdf(filtered_buildings, altitude, azimuth)
        
        # Filter microclimate features (trees/parks) in bbox
        micro_indices = self.microclimate_tree.query(bbox_poly)
        filtered_micro = self.microclimate_gdf.iloc[micro_indices]
        
        # Collect shade geometries
        shade_geoms = []
        if len(shadows_gdf) > 0:
            shade_geoms.extend(list(shadows_gdf['geometry']))
            
        for _, row in filtered_micro.iterrows():
            geom = row['geometry']
            if geom is None:
                continue
            if row.get('natural') == 'tree':
                # Buffer trees by 4m for canopy shade
                shade_geoms.append(geom.buffer(4.0))
            elif row.get('leisure') == 'park':
                shade_geoms.append(geom)
                
        # Union all shade geometries (building shadows and trees/parks)
        # This prevents double counting overlaps
        total_shade = None
        if len(shade_geoms) > 0:
            try:
                # Filter out any invalid geometries
                valid_shade_geoms = [g for g in shade_geoms if g is not None and g.is_valid]
                total_shade = unary_union(valid_shade_geoms)
            except Exception as e:
                print(f"Error unioning shade geometries: {e}")
                total_shade = None
            
        # Get local nodes inside bbox to speed up Dijkstra
        local_nodes = []
        for node in self.G.nodes():
            if min_x <= node[0] <= max_x and min_y <= node[1] <= max_y:
                local_nodes.append(node)
                
        # Ensure start/end nodes are included
        if source not in local_nodes:
            local_nodes.append(source)
        if target not in local_nodes:
            local_nodes.append(target)
            
        # Create local subgraph
        G_sub = self.G.subgraph(local_nodes).copy()
        
        # Calculate temperature scale factor (T_factor) for weight penalty:
        # scales the penalty linearly from 0.1 at 15°C to 1.0 at 30°C and up to 1.5 at 37.5°C
        t_factor = max(0.1, min(1.5, (temperature - 15.0) / 15.0))

        # Solar radiation heating effect based on sun elevation (altitude)
        rad_alt = math.radians(max(0.0, altitude))
        sin_alt = math.sin(rad_alt)
        delta_t_sun = 10.0 * sin_alt

        # Recalculate weights for the local subgraph
        for u, v, data in G_sub.edges(data=True):
            geom = data['geometry']
            length = data['length']
            surface = data.get('surface', 'paved')
            
            # Determine surface penalty multiplier
            if surface in ['asphalt', 'tar', 'compacted']:
                surface_factor = 1.5
            elif surface in ['concrete', 'paving_stones', 'paved', 'sett', 'cobblestone']:
                surface_factor = 1.2
            elif surface in ['grass', 'soil', 'wood', 'unpaved', 'sand', 'fine_gravel', 'bark', 'pebblestone']:
                surface_factor = 1.0
            else:
                surface_factor = 1.2
                
            # Intersect with the total shade geometry to find shaded length
            shade_len = 0.0
            if total_shade is not None and not total_shade.is_empty:
                try:
                    intersect = geom.intersection(total_shade)
                    if not intersect.is_empty:
                        shade_len = intersect.length
                except Exception:
                    # Fallback for geometry intersection errors
                    shade_len = 0.0
                    
            shade_factor = min(shade_len / length, 1.0) if length > 0 else 0.0
            
            # Cost function: Weight = D * (1 + beta * HeatPenalty * T_factor)
            heat_penalty = (1.0 - shade_factor) * surface_factor
            weight = length * (1.0 + beta * heat_penalty * t_factor)
            
            # Effective felt temperature on this segment
            t_eff = temperature + delta_t_sun * (1.0 - shade_factor) * surface_factor
            
            # Comfort scale: 1.0 (ideal, <=22°C) to 0.0 (worst, >=38°C)
            thermal_comfort = max(0.0, min(1.0, 1.0 - (t_eff - 22.0) / 16.0))
            
            data['weight'] = weight
            data['shade_factor'] = shade_factor
            data['shade_length'] = shade_len
            data['surface_factor'] = surface_factor
            data['t_eff'] = t_eff
            data['thermal_comfort'] = thermal_comfort
            
        # Compute shortest path
        try:
            path_nodes = nx.shortest_path(G_sub, source=source, target=target, weight='weight')
        except nx.NetworkXNoPath:
            # Fallback: try global graph with simple length weights if no local path exists
            try:
                print("No path in local sub-graph, falling back to global distance routing...")
                for u, v, data in self.G.edges(data=True):
                    data['weight'] = data['length']
                path_nodes = nx.shortest_path(self.G, source=source, target=target, weight='weight')
                G_sub = self.G # Reference the global graph for stats recovery
            except nx.NetworkXNoPath:
                return None
                
        # Reconstruct path geometry and compute route stats
        features = []
        total_len = 0.0
        total_shade_len = 0.0
        surface_types = {}
        
        for i in range(len(path_nodes) - 1):
            u = path_nodes[i]
            v = path_nodes[i+1]
            data = G_sub.get_edge_data(u, v)
            if data is None:
                continue
                
            geom = data['geometry']
            length = data['length']
            shade_length = data.get('shade_length', 0.0)
            shade_factor = data.get('shade_factor', 0.0)
            surface = data.get('surface', 'paved')
            t_eff = data.get('t_eff', temperature)
            thermal_comfort = data.get('thermal_comfort', 1.0)
            
            total_len += length
            total_shade_len += shade_length
            surface_types[surface] = surface_types.get(surface, 0.0) + length
            
            # Align LineString coordinates along the walk path traversal direction
            geom_coords = list(geom.coords)
            d_start_u = (geom_coords[0][0] - u[0])**2 + (geom_coords[0][1] - u[1])**2
            d_end_u = (geom_coords[-1][0] - u[0])**2 + (geom_coords[-1][1] - u[1])**2
            
            if d_end_u < d_start_u:
                geom_coords.reverse()
                
            wgs_coords = [transformer_to_wgs84.transform(pt[0], pt[1]) for pt in geom_coords]
            
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": wgs_coords
                },
                "properties": {
                    "shade_factor": shade_factor,
                    "shade_length": round(shade_length, 1),
                    "length": round(length, 1),
                    "surface": surface,
                    "t_eff": round(t_eff, 1),
                    "thermal_comfort": round(thermal_comfort, 2)
                }
            })
                
        # Format stats
        shade_pct = (total_shade_len / total_len * 100.0) if total_len > 0 else 0.0
        
        # Calculate Comfort Score based on length-weighted thermal comfort
        total_comfort_len = 0.0
        for feat in features:
            tc = feat["properties"]["thermal_comfort"]
            l = feat["properties"]["length"]
            total_comfort_len += tc * l
            
        avg_thermal_comfort = (total_comfort_len / total_len) if total_len > 0 else 1.0
        comfort_score = int(round(avg_thermal_comfort * 100))
        
        # Walking duration calculation (avg 1.3 m/s, slowing to 1.0 m/s in heat)
        avg_shade_factor = total_shade_len / total_len if total_len > 0 else 0.0
        walk_speed = 1.3 - 0.3 * (1.0 - avg_shade_factor)
        duration_min = (total_len / walk_speed) / 60.0
        
        geojson_route = {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "total_length": round(total_len, 1),
                "shade_length": round(total_shade_len, 1),
                "shade_percentage": round(shade_pct, 1),
                "comfort_score": comfort_score,
                "duration_minutes": round(duration_min, 1),
                "temperature": round(temperature, 1),
                "surface_breakdown": {k: round(v, 1) for k, v in surface_types.items()}
            }
        }
        
        return geojson_route

    def get_cool_parking(self, dest_lon, dest_lat, altitude, azimuth, temperature=22.0):
        """
        Finds and ranks parking lots within a 300m radius of the destination.
        Ranks parking spots dynamically based on coverage and ambient temperature.
        """
        transformer_to_metric = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
        transformer_to_wgs84 = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
        
        dest_x, dest_y = transformer_to_metric.transform(dest_lon, dest_lat)
        dest_point = Point(dest_x, dest_y)
        
        # Query parking spots within 300m buffer
        search_buffer = dest_point.buffer(300.0)
        parking_indices = self.parking_tree.query(search_buffer)
        
        if len(parking_indices) == 0:
            return {"type": "FeatureCollection", "features": []}
            
        filtered_parking = self.parking_gdf.iloc[parking_indices]
        
        # Also query building shadows in a larger neighborhood to check shade state of surface parkings
        building_indices = self.buildings_tree.query(dest_point.buffer(400.0))
        filtered_buildings = self.buildings_gdf.iloc[building_indices]
        shadows_gdf = shadow_projector.get_shadows_gdf(filtered_buildings, altitude, azimuth)
        
        shadow_union = None
        if len(shadows_gdf) > 0:
            shadow_union = unary_union(list(shadows_gdf['geometry']))
            
        # Query trees in 300m buffer
        micro_indices = self.microclimate_tree.query(search_buffer)
        filtered_micro = self.microclimate_gdf.iloc[micro_indices]
        tree_canopies = [row['geometry'].buffer(4.0) for _, row in filtered_micro.iterrows() if row.get('natural') == 'tree']
        tree_union = unary_union(tree_canopies) if len(tree_canopies) > 0 else None
        
        features = []
        for _, row in filtered_parking.iterrows():
            geom = row['geometry']
            if geom is None:
                continue
                
            centroid = geom.centroid
            # Convert geometry coordinates to WGS84 for GeoJSON output
            if isinstance(geom, Polygon):
                coords_wgs = [transformer_to_wgs84.transform(pt[0], pt[1]) for pt in geom.exterior.coords]
                geom_wgs = {"type": "Polygon", "coordinates": [coords_wgs]}
            elif isinstance(geom, Point):
                pt_wgs = transformer_to_wgs84.transform(geom.x, geom.y)
                geom_wgs = {"type": "Point", "coordinates": pt_wgs}
            else:
                continue
                
            p_type = row.get('parking', 'surface')
            if not isinstance(p_type, str) or p_type is None or (isinstance(p_type, float) and math.isnan(p_type)):
                p_type = 'surface'
                
            name = row.get('name', 'Parking Spot')
            if not isinstance(name, str) or name is None or (isinstance(name, float) and math.isnan(name)):
                name = 'Parking Spot'
            
            if p_type in ['underground', 'multi-storey', 'sheds', 'carport']:
                status = "excellent"
                description = "Covered / Garage (100% Shaded)"
                score = 100
            else:
                # Check if centroid is covered by building shadows or trees
                shaded_by_building = shadow_union is not None and shadow_union.contains(centroid)
                shaded_by_tree = tree_union is not None and tree_union.contains(centroid)
                
                # Check intersection percentage for precise coverage
                intersect_shadow = 0.0
                if shadow_union is not None:
                    try:
                        intersect_shadow = geom.intersection(shadow_union).area / geom.area
                    except Exception:
                        intersect_shadow = 1.0 if shaded_by_building else 0.0
                intersect_tree = 0.0
                if tree_union is not None:
                    try:
                        intersect_tree = geom.intersection(tree_union).area / geom.area
                    except Exception:
                        intersect_tree = 1.0 if shaded_by_tree else 0.0
                        
                total_coverage = min(1.0, intersect_shadow + intersect_tree)
                
                # Calculate felt temp and score
                rad_alt = math.radians(max(0.0, altitude))
                sin_alt = math.sin(rad_alt)
                delta_t_sun = 10.0 * sin_alt
                
                # Asphalt surface factor 1.5 for surface parking
                t_eff = temperature + delta_t_sun * (1.0 - total_coverage) * 1.5
                comfort = max(0.0, min(1.0, 1.0 - (t_eff - 22.0) / 16.0))
                score = int(round(comfort * 100))
                
                if score >= 80:
                    status = "excellent"
                    description = f"Cool parking ({int(total_coverage*100)}% shaded)"
                elif score >= 50:
                    status = "good"
                    description = f"Partially shaded ({int(total_coverage*100)}% shaded)"
                elif score >= 25:
                    status = "moderate"
                    description = f"Warm / Sunny ({int(total_coverage*100)}% shaded)"
                else:
                    status = "poor"
                    description = "Sunny open asphalt (Very Hot)"
                        
            features.append({
                "type": "Feature",
                "geometry": geom_wgs,
                "properties": {
                    "name": name,
                    "type": p_type,
                    "status": status,
                    "description": description,
                    "comfort_score": score
                }
            })
            
        return {"type": "FeatureCollection", "features": features}
