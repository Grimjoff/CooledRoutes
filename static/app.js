// State variables
let startMarker = null;
let endMarker = null;
let startCoords = null; // [lon, lat]
let endCoords = null; // [lon, lat]
let activeRouteData = null;
let activeParkingData = null;
let selectedParkingFeature = null;
let shadowFetchTimeout = null;

// DOM Elements
const timeSlider = document.getElementById('time-slider');
const timeVal = document.getElementById('time-val');
const comfortSlider = document.getElementById('comfort-slider');
const comfortVal = document.getElementById('comfort-val');
const startInput = document.getElementById('start-input');
const endInput = document.getElementById('end-input');
const btnClearStart = document.getElementById('btn-clear-start');
const btnClearEnd = document.getElementById('btn-clear-end');
const btnSwap = document.getElementById('btn-swap');
const btnClearAll = document.getElementById('btn-clear-all');
const statsPanel = document.getElementById('stats-panel');
const parkingPanel = document.getElementById('parking-panel');
const parkingList = document.getElementById('parking-list');
const toastEl = document.getElementById('toast');

// Center of Gießen, Germany
const GIESSEN_CENTER = [8.678, 50.584];

// Initialize MapLibre Map
const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    center: GIESSEN_CENTER,
    zoom: 14,
    minZoom: 12,
    maxZoom: 20
});

// Add Navigation controls
map.addControl(new maplibregl.NavigationControl(), 'top-right');

map.on('load', () => {
    console.log("Map loaded, setting up layers...");
    setupMapLayers();
    
    // Initial fetch of shadows and weather once map is loaded and viewport is ready
    setTimeout(() => {
        updateShadows();
        updateWeather();
    }, 500);
});

// Update shadows when map is panned or zoomed
map.on('moveend', () => {
    debounce(updateShadows, 300)();
});

// Map click handler to set start/end markers
map.on('click', (e) => {
    const coords = [e.lngLat.lng, e.lngLat.lat];
    
    // Check if user clicked a parking spot icon on the map
    const features = map.queryRenderedFeatures(e.point, { layers: ['parking-layer-fill', 'parking-layer-symbol'] });
    if (features.length > 0) {
        // Handled by the parking spot click handler
        return;
    }

    if (!startCoords) {
        setStart(coords);
        showToast("Start location set. Click to set destination.");
    } else if (!endCoords) {
        setEnd(coords);
        calculateRoute();
    } else {
        // If both are set, clicking moves the destination (End) point
        setEnd(coords);
        calculateRoute();
    }
});

// Setup custom map sources and layers
function setupMapLayers() {
    // 1. Shadows Source & Layer (Building shadows computed on the fly)
    map.addSource('shadows', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });
    map.addLayer({
        id: 'shadows-layer',
        type: 'fill',
        source: 'shadows',
        paint: {
            'fill-color': '#070a12',
            'fill-opacity': 0.65
        }
    });

    // 2. Microclimate Layer (Parks & Trees loaded statically from the server)
    map.addSource('microclimate', {
        type: 'geojson',
        data: '/data/giessen_microclimate.geojson'
    });
    
    // Parks styling (leisure=park)
    map.addLayer({
        id: 'parks-layer',
        type: 'fill',
        source: 'microclimate',
        filter: ['==', 'leisure', 'park'],
        paint: {
            'fill-color': '#10b981',
            'fill-opacity': 0.12,
            'fill-outline-color': 'rgba(16, 185, 129, 0.3)'
        }
    });

    // Trees styling (natural=tree)
    map.addLayer({
        id: 'trees-layer',
        type: 'circle',
        source: 'microclimate',
        filter: ['==', 'natural', 'tree'],
        paint: {
            'circle-color': '#10b981',
            'circle-opacity': 0.45,
            'circle-stroke-width': 1,
            'circle-stroke-color': 'rgba(16, 185, 129, 0.7)',
            // Dynamically scale tree canopy size based on zoom level
            'circle-radius': [
                'interpolate', ['linear'], ['zoom'],
                13, 1.5,
                15, 3.5,
                18, 9,
                20, 20
            ]
        }
    });

    // 3. Route Source & Layer (Calculated path segmented by shade)
    map.addSource('route', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });
    
    // Base route layer (for styling outline/glow)
    map.addLayer({
        id: 'route-glow',
        type: 'line',
        source: 'route',
        layout: {
            'line-join': 'round',
            'line-cap': 'round'
        },
        paint: {
            'line-color': '#000000',
            'line-width': 9,
            'line-opacity': 0.4
        }
    });

    // Active route segment layer with dynamic coloring based on thermal comfort index
    map.addLayer({
        id: 'route-layer',
        type: 'line',
        source: 'route',
        layout: {
            'line-join': 'round',
            'line-cap': 'round'
        },
        paint: {
            'line-width': 6,
            'line-color': [
                'interpolate',
                ['linear'],
                ['get', 'thermal_comfort'],
                0.0, '#ef4444', // Hot / Uncomfortable: Red
                0.5, '#f59e0b', // Warm / Moderate: Amber
                1.0, '#10b981'  // Comfortable / Cool: Emerald Green
            ]
        }
    });

    // 4. Parking Spots Source & Layer
    map.addSource('parking', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
    });

    map.addLayer({
        id: 'parking-layer-fill',
        type: 'fill',
        source: 'parking',
        filter: ['==', '$type', 'Polygon'],
        paint: {
            'fill-color': [
                'match',
                ['get', 'status'],
                'excellent', '#10b981',
                'good', '#3b82f6',
                'moderate', '#f59e0b',
                'poor', '#ef4444',
                '#9ca3af'
            ],
            'fill-opacity': 0.4,
            'fill-outline-color': '#ffffff'
        }
    });

    map.addLayer({
        id: 'parking-layer-symbol',
        type: 'circle',
        source: 'parking',
        paint: {
            'circle-color': [
                'match',
                ['get', 'status'],
                'excellent', '#10b981',
                'good', '#3b82f6',
                'moderate', '#f59e0b',
                'poor', '#ef4444',
                '#9ca3af'
            ],
            'circle-radius': 8,
            'circle-stroke-width': 2,
            'circle-stroke-color': '#ffffff',
            'circle-opacity': 0.9
        }
    });

    // Add interactivity to parking features
    map.on('click', 'parking-layer-symbol', handleParkingFeatureClick);
    map.on('click', 'parking-layer-fill', handleParkingFeatureClick);

    // Change cursor on hover
    map.on('mouseenter', 'parking-layer-symbol', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'parking-layer-symbol', () => map.getCanvas().style.cursor = '');
    map.on('mouseenter', 'parking-layer-fill', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'parking-layer-fill', () => map.getCanvas().style.cursor = '');
}

// Set Start Marker
function setStart(coords) {
    startCoords = coords;
    startInput.value = `${coords[0].toFixed(5)}, ${coords[1].toFixed(5)}`;
    
    // Fetch weather for the new start coordinates
    updateWeather();
    
    if (startMarker) {
        startMarker.setLngLat(coords);
    } else {
        const el = document.createElement('div');
        el.className = 'maplibre-marker';
        el.innerHTML = '<div class="pulse-marker-start"></div>';
        
        startMarker = new maplibregl.Marker({
            element: el,
            draggable: true
        })
        .setLngLat(coords)
        .addTo(map);

        startMarker.on('dragend', () => {
            const newCoords = startMarker.getLngLat();
            setStart([newCoords.lng, newCoords.lat]);
            if (endCoords) calculateRoute();
        });
    }
}

// Set End Marker
function setEnd(coords) {
    endCoords = coords;
    endInput.value = `${coords[0].toFixed(5)}, ${coords[1].toFixed(5)}`;
    
    if (endMarker) {
        endMarker.setLngLat(coords);
    } else {
        const el = document.createElement('div');
        el.className = 'maplibre-marker';
        el.innerHTML = '<div class="pulse-marker-end"></div>';

        endMarker = new maplibregl.Marker({
            element: el,
            draggable: true
        })
        .setLngLat(coords)
        .addTo(map);

        endMarker.on('dragend', () => {
            const newCoords = endMarker.getLngLat();
            setEnd([newCoords.lng, newCoords.lat]);
            calculateRoute();
        });
    }
}

// Format time slider value to HH:MM
function formatHour(hourVal) {
    const hours = Math.floor(hourVal);
    const minutes = Math.floor((hourVal - hours) * 60);
    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}`;
}

// Generate an ISO string representing selected time on today's date
function getSelectedDateTimeISO() {
    const hourVal = parseFloat(timeSlider.value);
    const hours = Math.floor(hourVal);
    const minutes = Math.floor((hourVal - hours) * 60);
    
    const now = new Date();
    now.setHours(hours, minutes, 0, 0);
    return now.toISOString();
}

// Update Comfort slider badge
function updateComfortBadge(val) {
    if (val === 0) comfortVal.textContent = "Shortest Distance";
    else if (val <= 3) comfortVal.textContent = "Slight Shade Avoidance";
    else if (val <= 7) comfortVal.textContent = "Balanced Comfort";
    else comfortVal.textContent = "Maximum Shade / Cool Pavement";
}

// Fetch projected building shadows from backend and render
async function updateShadows() {
    // Only query shadows at higher zoom levels to avoid overloading backend
    if (map.getZoom() < 14) {
        map.getSource('shadows').setData({ type: 'FeatureCollection', features: [] });
        return;
    }

    const bounds = map.getBounds();
    const timeStr = getSelectedDateTimeISO();
    const url = `/api/shadows?time=${encodeURIComponent(timeStr)}` + 
                `&min_lon=${bounds.getWest()}&min_lat=${bounds.getSouth()}` +
                `&max_lon=${bounds.getEast()}&max_lat=${bounds.getNorth()}`;

    try {
        const response = await fetch(url);
        if (response.ok) {
            const shadowsData = await response.json();
            map.getSource('shadows').setData(shadowsData);
        }
    } catch (err) {
        console.error("Failed to fetch building shadows:", err);
    }
}

// Main API call: Calculate route between start and destination
async function calculateRoute() {
    if (!startCoords || !endCoords) return;

    showToast("Calculating shadiest route...");
    const timeStr = getSelectedDateTimeISO();
    const beta = parseFloat(comfortSlider.value);

    try {
        const response = await fetch('/api/route', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                start_lon: startCoords[0],
                start_lat: startCoords[1],
                end_lon: endCoords[0],
                end_lat: endCoords[1],
                beta: beta,
                time: timeStr
            })
        });

        if (response.ok) {
            activeRouteData = await response.json();
            map.getSource('route').setData(activeRouteData);
            displayStats(activeRouteData.properties);
            
            // If the user set a destination, search for cool parking spots nearby
            fetchCoolParking(endCoords[0], endCoords[1], timeStr);
        } else {
            const error = await response.json();
            showToast(`Routing failed: ${error.detail || 'Path not found'}`);
            clearRouteGeometry();
        }
    } catch (err) {
        console.error("Failed to calculate route:", err);
        showToast("Error connecting to routing server.");
        clearRouteGeometry();
    }
}

// Fetch cool parking spots near destination
async function fetchCoolParking(lon, lat, timeStr) {
    try {
        const url = `/api/parking?dest_lon=${lon}&dest_lat=${lat}&time=${encodeURIComponent(timeStr)}`;
        const response = await fetch(url);
        
        if (response.ok) {
            activeParkingData = await response.json();
            map.getSource('parking').setData(activeParkingData);
            displayParkingList(activeParkingData.features);
        }
    } catch (err) {
        console.error("Failed to fetch parking spots:", err);
    }
}

// Display route stats in panel
function displayStats(props) {
    statsPanel.classList.remove('hidden');
    
    // Distance
    const distanceMeters = props.total_length;
    if (distanceMeters >= 1000) {
        document.getElementById('stat-distance').textContent = `${(distanceMeters/1000).toFixed(2)} km`;
    } else {
        document.getElementById('stat-distance').textContent = `${distanceMeters.toFixed(0)} m`;
    }

    // Walking time
    const duration = props.duration_minutes;
    document.getElementById('stat-duration').textContent = `${Math.ceil(duration)} min`;

    // Shade coverage
    const shadePct = props.shade_percentage;
    document.getElementById('stat-shade-pct').textContent = `${shadePct.toFixed(0)}%`;
    
    // Shade distance
    const shadeM = props.shade_length;
    if (shadeM >= 1000) {
        document.getElementById('stat-shade-dist').textContent = `${(shadeM/1000).toFixed(2)} km`;
    } else {
        document.getElementById('stat-shade-dist').textContent = `${shadeM.toFixed(0)} m`;
    }
    document.getElementById('shade-progress-fill').style.width = `${shadePct}%`;

    // Temperature
    const temp = props.temperature;
    document.getElementById('stat-temp').textContent = `${temp.toFixed(1)} °C`;

    // Comfort Score
    const score = props.comfort_score;
    const comfortEl = document.getElementById('stat-comfort');
    comfortEl.textContent = `${score}/100`;
    
    if (score >= 80) comfortEl.className = 'stat-val text-green';
    else if (score >= 50) comfortEl.className = 'stat-val text-blue';
    else comfortEl.className = 'stat-val text-amber';

    // Surface Breakdown pills
    const container = document.getElementById('surface-pills-container');
    container.innerHTML = '';
    
    for (const [surface, len] of Object.entries(props.surface_breakdown)) {
        let dotClass = 'dot-default';
        if (surface === 'asphalt' || surface === 'tar') dotClass = 'dot-asphalt';
        else if (surface === 'paving_stones' || surface === 'concrete' || surface === 'paved') dotClass = 'dot-paving_stones';
        else if (surface === 'grass' || surface === 'soil' || surface === 'wood') dotClass = 'dot-green';
        
        const label = surface.replace('_', ' ');
        const lenStr = len >= 1000 ? `${(len/1000).toFixed(1)}km` : `${len.toFixed(0)}m`;
        
        const pill = document.createElement('span');
        pill.className = 'surface-pill';
        pill.innerHTML = `<span class="surface-dot ${dotClass}"></span>${label}: ${lenStr}`;
        container.appendChild(pill);
    }
}

// Display suggested parking spots in list
function displayParkingList(features) {
    if (features.length === 0) {
        parkingPanel.classList.add('hidden');
        return;
    }
    
    parkingPanel.classList.remove('hidden');
    parkingList.innerHTML = '';

    // Sort features by score descending
    const sortedFeatures = [...features].sort((a, b) => b.properties.comfort_score - a.properties.comfort_score);

    sortedFeatures.forEach((feat, index) => {
        const props = feat.properties;
        const item = document.createElement('div');
        item.className = 'parking-item';
        if (selectedParkingFeature && selectedParkingFeature.properties.name === props.name) {
            item.classList.add('selected');
        }

        item.innerHTML = `
            <div class="parking-info">
                <span class="parking-name">${props.name}</span>
                <span class="parking-desc">${props.description} (${props.type})</span>
            </div>
            <span class="parking-badge badge-${props.status}">${props.comfort_score} Pts</span>
        `;

        item.addEventListener('click', () => {
            selectParking(feat, item);
        });

        parkingList.appendChild(item);
    });
}

// Map layer parking spot click handler
function handleParkingFeatureClick(e) {
    if (e.features.length === 0) return;
    const feat = e.features[0];
    
    // Find matching DOM item and click it
    const items = document.querySelectorAll('.parking-item');
    for (let item of items) {
        const name = item.querySelector('.parking-name').textContent;
        if (name === feat.properties.name) {
            item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            selectParking(feat, item);
            break;
        }
    }
}

// Select a parking spot: Update start to the parking location
// This completes the transition: Drive to Shaded Parking Spot -> Walk Cool Route to Destination
function selectParking(feature, domElement) {
    // Clear previous selection
    document.querySelectorAll('.parking-item').forEach(i => i.classList.remove('selected'));
    domElement.classList.add('selected');
    selectedParkingFeature = feature;

    // Get coords of the parking spot
    let parkCoords;
    if (feature.geometry.type === 'Point') {
        parkCoords = feature.geometry.coordinates;
    } else if (feature.geometry.type === 'Polygon') {
        // Calculate polygon centroid or take first coordinate
        const coords = feature.geometry.coordinates[0];
        let sumLon = 0, sumLat = 0;
        coords.forEach(pt => {
            sumLon += pt[0];
            sumLat += pt[1];
        });
        parkCoords = [sumLon / coords.length, sumLat / coords.length];
    }
    
    if (parkCoords) {
        // Re-route from parking spot to original destination
        // Store parking spot coordinates as new Start
        setStart(parkCoords);
        startInput.value = `Parked: ${feature.properties.name}`;
        showToast(`Selected parking. Walk route recalculated from ${feature.properties.name}!`);
        calculateRoute();
    }
}

// Reset route layers
function clearRouteGeometry() {
    map.getSource('route').setData({ type: 'FeatureCollection', features: [] });
    map.getSource('parking').setData({ type: 'FeatureCollection', features: [] });
    statsPanel.classList.add('hidden');
    parkingPanel.classList.add('hidden');
    selectedParkingFeature = null;
}

// Reset all state
function clearAll() {
    if (startMarker) { startMarker.remove(); startMarker = null; }
    if (endMarker) { endMarker.remove(); endMarker = null; }
    startCoords = null;
    endCoords = null;
    startInput.value = '';
    endInput.value = '';
    clearRouteGeometry();
    showToast("Route cleared. Click map to set new start location.");
}

// Fetch weather temperature for start location or map center and update UI
async function updateWeather() {
    const lat = startCoords ? startCoords[1] : map.getCenter().lat;
    const lon = startCoords ? startCoords[0] : map.getCenter().lng;
    const timeStr = getSelectedDateTimeISO();
    
    const url = `/api/weather?lat=${lat}&lon=${lon}&time=${encodeURIComponent(timeStr)}`;
    
    try {
        const response = await fetch(url);
        if (response.ok) {
            const data = await response.json();
            document.getElementById('temp-value').textContent = data.temperature.toFixed(1);
            document.getElementById('temp-desc').textContent = data.description;
            
            // Dynamically change icon or colors based on temperature
            const iconEl = document.querySelector('.weather-temp-val i');
            if (iconEl) {
                iconEl.className = 'fa-solid';
                if (data.temperature >= 32) {
                    iconEl.classList.add('fa-temperature-high');
                    iconEl.style.color = '#ef4444';
                } else if (data.temperature >= 20) {
                    iconEl.classList.add('fa-cloud-sun');
                    iconEl.style.color = '#f59e0b';
                } else {
                    iconEl.classList.add('fa-temperature-low');
                    iconEl.style.color = '#3b82f6';
                }
            }
        }
    } catch (err) {
        console.error("Failed to fetch weather:", err);
    }
}

// Event Listeners
timeSlider.addEventListener('input', (e) => {
    const val = parseFloat(e.target.value);
    timeVal.textContent = formatHour(val);
});

timeSlider.addEventListener('change', () => {
    updateShadows();
    updateWeather();
    if (startCoords && endCoords) calculateRoute();
});

comfortSlider.addEventListener('input', (e) => {
    const val = parseInt(e.target.value);
    updateComfortBadge(val);
});

comfortSlider.addEventListener('change', () => {
    if (startCoords && endCoords) calculateRoute();
});

btnClearStart.addEventListener('click', () => {
    if (startMarker) { startMarker.remove(); startMarker = null; }
    startCoords = null;
    startInput.value = '';
    clearRouteGeometry();
});

btnClearEnd.addEventListener('click', () => {
    if (endMarker) { endMarker.remove(); endMarker = null; }
    endCoords = null;
    endInput.value = '';
    clearRouteGeometry();
});

btnSwap.addEventListener('click', () => {
    if (!startCoords || !endCoords) return;
    const temp = startCoords;
    setStart(endCoords);
    setEnd(temp);
    calculateRoute();
});

btnClearAll.addEventListener('click', clearAll);

// Helper: Toast message
function showToast(message) {
    toastEl.textContent = message;
    toastEl.classList.remove('hidden');
    
    // Clear previous timeout if exists
    if (toastEl.timeoutId) clearTimeout(toastEl.timeoutId);
    
    toastEl.timeoutId = setTimeout(() => {
        toastEl.classList.add('hidden');
    }, 3000);
}

// Helper: Debouncer for performance optimization
function debounce(func, wait) {
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(shadowFetchTimeout);
            func(...args);
        };
        clearTimeout(shadowFetchTimeout);
        shadowFetchTimeout = setTimeout(later, wait);
    };
}
