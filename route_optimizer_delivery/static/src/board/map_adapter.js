/** @odoo-module **/
import { loadJS, loadCSS } from "@web/core/assets";

/**
 * Mapa del tablero con dos motores intercambiables: Leaflet + OpenStreetMap (sin clave)
 * y Google Maps (con clave: suma tráfico en vivo y satélite). El tablero habla siempre
 * con la misma interfaz:
 *
 *   render({ depot, lines, pins, trucks })   dibuja todo de nuevo
 *   fit(points) / panTo(lat, lon)            encuadre
 *   project(lat, lon) -> {x, y}              píxeles dentro del contenedor (para el lazo)
 *   setInteractive(bool)                     congela el mapa mientras se dibuja el lazo
 *   setTraffic(bool) / supportsTraffic
 *   onPinClick(fn) / destroy()
 */

const LEAFLET = "/route_optimizer_delivery/static/lib/leaflet/leaflet";

/** Polilínea codificada de Google/OSRM (precisión 5) → [[lat, lon], ...]. */
export function decodePolyline(encoded) {
    const points = [];
    let index = 0;
    let lat = 0;
    let lon = 0;
    const next = () => {
        let result = 0;
        let shift = 0;
        let byte;
        do {
            byte = encoded.charCodeAt(index++) - 63;
            result |= (byte & 0x1f) << shift;
            shift += 5;
        } while (byte >= 0x20);
        return result & 1 ? ~(result >> 1) : result >> 1;
    };
    while (index < encoded.length) {
        lat += next();
        lon += next();
        points.push([lat / 1e5, lon / 1e5]);
    }
    return points;
}

function pinHtml(pin) {
    const classes = ["o_rdb_pin"];
    if (pin.done) {
        classes.push("o_rdb_pin_done");
    }
    if (pin.selected) {
        classes.push("o_rdb_pin_selected");
    }
    if (pin.kind) {
        classes.push("o_rdb_pin_" + pin.kind);
    }
    const el = document.createElement("div");
    el.className = classes.join(" ");
    el.style.background = pin.color;
    el.textContent = pin.label;
    return el;
}

class LeafletMap {
    static async load() {
        await Promise.all([loadCSS(LEAFLET + ".css"), loadJS(LEAFLET + ".js")]);
    }

    constructor(el, config) {
        const L = window.L;
        this.map = L.map(el, { zoomControl: false, attributionControl: true });
        L.control.zoom({ position: "bottomright" }).addTo(this.map);
        L.tileLayer(config.tile_url, {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        }).addTo(this.map);
        this.layers = L.layerGroup().addTo(this.map);
        this.map.setView([-34.6, -58.4], 11);
        this.supportsTraffic = false;
        this.pinClick = () => {};
        this.viewChange = () => {};
        this.map.on("move zoom", () => this.viewChange());
    }

    onPinClick(fn) {
        this.pinClick = fn;
    }

    onViewChange(fn) {
        this.viewChange = fn;
    }

    render({ depot, lines, pins, trucks }) {
        const L = window.L;
        this.layers.clearLayers();
        for (const line of lines) {
            L.polyline(line.path, {
                color: line.color,
                weight: line.dashed ? 3 : 4,
                opacity: line.faded ? 0.3 : 0.85,
                dashArray: line.dashed ? "6 8" : null,
            }).addTo(this.layers);
        }
        if (depot) {
            const el = document.createElement("div");
            el.className = "o_rdb_pin o_rdb_pin_depot";
            el.innerHTML = '<i class="o_rdi o_rdi-home"></i>';
            L.marker([depot.lat, depot.lon], { icon: this.icon(el, 34), title: depot.name }).addTo(this.layers);
        }
        for (const pin of pins) {
            const marker = L.marker([pin.lat, pin.lon], {
                icon: this.icon(pinHtml(pin), 28),
                title: pin.title,
                zIndexOffset: pin.selected ? 500 : 0,
            });
            marker.on("click", () => this.pinClick(pin.id));
            marker.addTo(this.layers);
        }
        for (const truck of trucks) {
            const el = document.createElement("div");
            el.className = "o_rdb_truck";
            el.style.background = truck.color;
            el.innerHTML = '<i class="o_rdi o_rdi-truck"></i>';
            const label = document.createElement("span");
            label.textContent = truck.label;
            el.append(label);
            L.marker([truck.lat, truck.lon], {
                icon: L.divIcon({ className: "", html: el.outerHTML, iconSize: [0, 0] }),
                title: truck.title,
                zIndexOffset: 1000,
            }).addTo(this.layers);
        }
    }

    icon(el, size) {
        return window.L.divIcon({ className: "", html: el.outerHTML, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
    }

    fit(points) {
        this.map.invalidateSize();
        if (points.length) {
            this.map.fitBounds(points, { padding: [40, 40], maxZoom: 15 });
        }
    }

    panTo(lat, lon) {
        this.map.panTo([lat, lon]);
    }

    project(lat, lon) {
        return this.map.latLngToContainerPoint([lat, lon]);
    }

    setInteractive(active) {
        const method = active ? "enable" : "disable";
        for (const handler of ["dragging", "scrollWheelZoom", "doubleClickZoom", "touchZoom", "boxZoom"]) {
            this.map[handler][method]();
        }
    }

    setTraffic() {}

    resize() {
        this.map.invalidateSize();
    }

    destroy() {
        this.map.remove();
    }
}

let googleLoading = null;

class GoogleMap {
    static load(config) {
        if (window.google && window.google.maps && window.google.maps.Map) {
            return Promise.resolve();
        }
        if (!googleLoading) {
            googleLoading = new Promise((resolve, reject) => {
                window.__routeDispatchGoogleReady = resolve;
                const script = document.createElement("script");
                script.src =
                    "https://maps.googleapis.com/maps/api/js?v=weekly&loading=async&callback=__routeDispatchGoogleReady&key=" +
                    encodeURIComponent(config.google_key);
                script.async = true;
                script.onerror = () => {
                    googleLoading = null;
                    reject(new Error("No se pudo cargar Google Maps"));
                };
                document.head.append(script);
            });
        }
        return googleLoading;
    }

    constructor(el) {
        const maps = window.google.maps;
        this.el = el;
        this.map = new maps.Map(el, {
            center: { lat: -34.6, lng: -58.4 },
            zoom: 11,
            mapTypeControl: true,
            mapTypeControlOptions: { position: maps.ControlPosition.TOP_RIGHT },
            streetViewControl: false,
            fullscreenControl: false,
            clickableIcons: false,
            gestureHandling: "greedy",
        });
        this.traffic = new maps.TrafficLayer();
        this.supportsTraffic = true;
        this.items = [];
        this.pinClick = () => {};
        this.viewChange = () => {};
        // Una capa propia da la proyección a píxeles (para el lazo) y un lugar donde
        // poner los pines como HTML, iguales a los de OpenStreetMap.
        const adapter = this;
        class PinLayer extends maps.OverlayView {
            onAdd() {
                this.box = document.createElement("div");
                this.box.className = "o_rdb_gpins";
                this.getPanes().overlayMouseTarget.append(this.box);
            }
            draw() {
                adapter.placePins();
                adapter.viewChange();
            }
            onRemove() {
                this.box.remove();
            }
        }
        this.layer = new PinLayer();
        this.layer.setMap(this.map);
        this.pins = [];
    }

    onPinClick(fn) {
        this.pinClick = fn;
    }

    onViewChange(fn) {
        this.viewChange = fn;
    }

    render({ depot, lines, pins, trucks }) {
        const maps = window.google.maps;
        for (const item of this.items) {
            item.setMap(null);
        }
        this.items = lines.map(
            (line) =>
                new maps.Polyline({
                    map: this.map,
                    path: line.path.map(([lat, lng]) => ({ lat, lng })),
                    strokeColor: line.color,
                    strokeWeight: line.dashed ? 3 : 4,
                    strokeOpacity: line.dashed ? 0 : line.faded ? 0.3 : 0.85,
                    icons: line.dashed
                        ? [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 0.9, scale: 3 }, offset: "0", repeat: "14px" }]
                        : null,
                })
        );
        this.pins = [];
        if (depot) {
            const el = document.createElement("div");
            el.className = "o_rdb_pin o_rdb_pin_depot";
            el.innerHTML = '<i class="o_rdi o_rdi-home"></i>';
            el.title = depot.name;
            this.pins.push({ lat: depot.lat, lon: depot.lon, el });
        }
        for (const pin of pins) {
            const el = pinHtml(pin);
            el.title = pin.title;
            el.addEventListener("click", () => this.pinClick(pin.id));
            this.pins.push({ lat: pin.lat, lon: pin.lon, el, z: pin.selected ? 5 : 1 });
        }
        for (const truck of trucks) {
            const el = document.createElement("div");
            el.className = "o_rdb_truck";
            el.style.background = truck.color;
            el.title = truck.title;
            el.innerHTML = '<i class="o_rdi o_rdi-truck"></i>';
            const label = document.createElement("span");
            label.textContent = truck.label;
            el.append(label);
            this.pins.push({ lat: truck.lat, lon: truck.lon, el, z: 9, anchor: "truck" });
        }
        if (this.layer.box) {
            this.layer.box.replaceChildren(...this.pins.map((pin) => pin.el));
            this.placePins();
        }
    }

    placePins() {
        const projection = this.layer.getProjection();
        if (!projection || !this.layer.box) {
            return;
        }
        if (this.layer.box.childElementCount !== this.pins.length) {
            this.layer.box.replaceChildren(...this.pins.map((pin) => pin.el));
        }
        for (const pin of this.pins) {
            const point = projection.fromLatLngToDivPixel(new window.google.maps.LatLng(pin.lat, pin.lon));
            pin.el.style.position = "absolute";
            pin.el.style.left = point.x + "px";
            pin.el.style.top = point.y + "px";
            pin.el.style.zIndex = pin.z || 1;
            pin.el.style.transform = "translate(-50%, -50%)";
        }
    }

    fit(points) {
        if (!points.length) {
            return;
        }
        const bounds = new window.google.maps.LatLngBounds();
        for (const [lat, lng] of points) {
            bounds.extend({ lat, lng });
        }
        this.map.fitBounds(bounds, 48);
    }

    panTo(lat, lon) {
        this.map.panTo({ lat, lng: lon });
    }

    project(lat, lon) {
        const projection = this.layer.getProjection();
        if (!projection) {
            return { x: -1, y: -1 };
        }
        const point = projection.fromLatLngToContainerPixel(new window.google.maps.LatLng(lat, lon));
        return { x: point.x, y: point.y };
    }

    setInteractive(active) {
        this.map.setOptions({ gestureHandling: active ? "greedy" : "none", keyboardShortcuts: active });
    }

    setTraffic(on) {
        this.traffic.setMap(on ? this.map : null);
    }

    resize() {}

    destroy() {
        this.layer.setMap(null);
    }
}

/** Crea el mapa del motor configurado; si Google no carga, cae a OpenStreetMap. */
export async function createMap(el, config) {
    if (config.provider === "google") {
        try {
            await GoogleMap.load(config);
            return new GoogleMap(el, config);
        } catch {
            // sigue con OpenStreetMap
        }
    }
    await LeafletMap.load();
    return new LeafletMap(el, config);
}
