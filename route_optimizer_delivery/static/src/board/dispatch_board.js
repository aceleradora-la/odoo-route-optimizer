import { Component, useState, useRef, onWillStart, onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS, loadCSS } from "@web/core/assets";
import { hourLabel } from "../app/delivery_app";

const MODEL = "stock.picking.batch";
const REFRESH_MS = 30000;
const LEAFLET = "/route_optimizer_delivery/static/lib/leaflet/leaflet";
// Colores bien separados entre sí para distinguir rutas en el mapa y en las columnas.
const ROUTE_COLORS = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#4d7c0f"];
const UNASSIGNED_COLOR = "#6b7280";

function isoDay(date) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * Tablero de despacho: las rutas del día en columnas y en el mapa. Las paradas pendientes
 * se arrastran para cambiar el orden, pasarlas a otra ruta, sacarlas o abrir una ruta nueva.
 */
export class RouteDispatchBoard extends Component {
    static template = "route_optimizer_delivery.RouteDispatchBoard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.mapRef = useRef("map");
        this.state = useState({
            day: isoDay(new Date()),
            routes: [],
            unassigned: [],
            depot: false,
            loading: true,
            drop: null, // { batchId, index } donde caería la parada arrastrada
            selectedId: null,
        });
        this.drag = null;
        this.tileUrl = "";
        this.layers = null;
        this.markers = {};
        this.fitted = null;
        onWillStart(async () => {
            await Promise.all([this.load(), this.loadLeaflet()]);
        });
        onMounted(() => {
            this.initMap();
            this.drawMap();
            this.timer = setInterval(() => {
                if (!this.drag) {
                    this.load();
                }
            }, REFRESH_MS);
        });
        // Solo se redibuja con datos nuevos: seleccionar una parada también repinta el
        // componente, y borrar las capas le cerraría el globo recién abierto.
        onPatched(() => {
            if (this.mapDirty) {
                this.drawMap();
            }
        });
        onWillUnmount(() => {
            clearInterval(this.timer);
            if (this.map) {
                this.map.remove();
            }
        });
    }

    // ------------------------------------------------------------------
    // Datos
    // ------------------------------------------------------------------
    async loadLeaflet() {
        try {
            await Promise.all([loadCSS(LEAFLET + ".css"), loadJS(LEAFLET + ".js")]);
        } catch {
            // Sin mapa el tablero sigue sirviendo.
        }
    }

    async load() {
        try {
            const data = await this.orm.call(MODEL, "route_dispatch_data", [this.state.day]);
            this.tileUrl = data.tile_url;
            this.mapDirty = true;
            Object.assign(this.state, {
                routes: data.routes,
                unassigned: data.unassigned,
                depot: data.depot,
                loading: false,
            });
        } catch (error) {
            this.state.loading = false;
            throw error;
        }
    }

    async setDay(day) {
        if (!day) {
            return;
        }
        this.state.day = day;
        this.fitted = null;
        await this.load();
    }

    shiftDay(delta) {
        const date = new Date(this.state.day + "T12:00:00");
        date.setDate(date.getDate() + delta);
        return this.setDay(isoDay(date));
    }

    today() {
        return this.setDay(isoDay(new Date()));
    }

    get dayLabel() {
        const label = new Date(this.state.day + "T12:00:00").toLocaleDateString("es-AR", {
            weekday: "long",
            day: "numeric",
            month: "long",
        });
        return label.charAt(0).toUpperCase() + label.slice(1);
    }

    get totals() {
        const sum = (key) => this.state.routes.reduce((acc, route) => acc + route[key], 0);
        return {
            routes: this.state.routes.length,
            stops: sum("stop_count"),
            done: sum("done_count"),
            failed: sum("failed_count"),
            unassigned: this.state.unassigned.length,
        };
    }

    color(route) {
        return ROUTE_COLORS[this.state.routes.indexOf(route) % ROUTE_COLORS.length];
    }

    hour(value) {
        return hourLabel(value);
    }

    stateLabel(stop) {
        return { delivered: "Entregado", partial: "Parcial", failed: "No entregado" }[stop.state] || "";
    }

    routeStatus(route) {
        if (route.finished) {
            return "Terminada";
        }
        return route.started ? "En curso" : "Sin salir";
    }

    positionAge(route) {
        if (!route.position) {
            return "";
        }
        const minutes = Math.max(0, Math.round((Date.now() - new Date(route.position.at.replace(" ", "T") + "Z")) / 60000));
        return minutes < 1 ? "posición de recién" : `posición de hace ${minutes} min`;
    }

    // ------------------------------------------------------------------
    // Acciones
    // ------------------------------------------------------------------
    async optimize(route) {
        const action = await this.orm.call(MODEL, "action_route_optimizer_wizard", [[route.id]]);
        // Una acción pedida por RPC no pasa por el armado de vistas del servidor.
        action.views = [[false, "form"]];
        this.action.doAction(action, { onClose: () => this.load() });
    }

    openRoute(route) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: MODEL,
            res_id: route.id,
            views: [[false, "form"]],
        });
    }

    openStop(stop) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.picking",
            res_id: stop.id,
            views: [[false, "form"]],
        });
    }

    selectStop(stop) {
        this.state.selectedId = stop.id;
        const marker = this.markers[stop.id];
        if (marker && this.map) {
            this.map.panTo(marker.getLatLng());
            marker.openPopup();
        }
    }

    // ------------------------------------------------------------------
    // Arrastrar y soltar
    // ------------------------------------------------------------------
    onDragStart(stop, batchId, ev) {
        if (stop.state !== "pending") {
            ev.preventDefault();
            return;
        }
        this.drag = { id: stop.id, batchId };
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", String(stop.id));
    }

    onDragEnd() {
        this.drag = null;
        this.state.drop = null;
    }

    /** `index`: posición entre las paradas de la columna sin contar la arrastrada. */
    onDragOver(batchId, index, ev) {
        if (!this.drag) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        ev.dataTransfer.dropEffect = "move";
        const drop = this.state.drop;
        if (!drop || drop.batchId !== batchId || drop.index !== index) {
            this.state.drop = { batchId, index };
        }
    }

    /** Índice de la tarjeta dentro de su columna, sin contar la parada que se arrastra. */
    cardIndex(route, stop) {
        return route.stops.filter((item) => item.id !== this.drag?.id).indexOf(stop);
    }

    isDropBefore(batchId, route, stop) {
        const drop = this.state.drop;
        return Boolean(drop && this.drag && drop.batchId === batchId && drop.index === this.cardIndex(route, stop));
    }

    isDropEnd(batchId, count) {
        const drop = this.state.drop;
        return Boolean(drop && drop.batchId === batchId && drop.index >= count);
    }

    async onDrop(batchId, index, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const drag = this.drag;
        this.onDragEnd();
        if (!drag) {
            return;
        }
        try {
            if (batchId === "new") {
                await this.orm.call(MODEL, "route_dispatch_new_route", [this.state.day, drag.id]);
            } else {
                await this.orm.call(MODEL, "route_dispatch_move", [drag.id, batchId || false, index]);
            }
        } finally {
            await this.load();
        }
    }

    // ------------------------------------------------------------------
    // Mapa
    // ------------------------------------------------------------------
    initMap() {
        const L = window.L;
        if (!L || !this.mapRef.el) {
            return;
        }
        this.map = L.map(this.mapRef.el, { zoomControl: true, attributionControl: true });
        L.tileLayer(this.tileUrl, {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        }).addTo(this.map);
        this.layers = L.layerGroup().addTo(this.map);
        this.map.setView([-34.6, -58.4], 11);
    }

    pin(label, color, extraClass = "") {
        return window.L.divIcon({
            className: "",
            html: `<div class="o_rdb_pin ${extraClass}" style="background:${color}">${label}</div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
        });
    }

    addStopMarker(stop, label, color, title) {
        const L = window.L;
        if (!stop.lat && !stop.lon) {
            return null;
        }
        const done = stop.state !== "pending";
        const marker = L.marker([stop.lat, stop.lon], {
            icon: this.pin(label, color, done ? "o_rdb_pin_done" : ""),
            title: stop.partner,
        });
        const popup = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = `${label}. ${stop.partner}`;
        const detail = document.createElement("div");
        detail.textContent = [title, stop.address, stop.eta && done === false ? "Estimado " + hourLabel(stop.eta) : this.stateLabel(stop)]
            .filter(Boolean)
            .join(" · ");
        popup.append(name, detail);
        marker.bindPopup(popup);
        marker.on("click", () => {
            this.state.selectedId = stop.id;
            document.querySelector(`.o_rdb_card[data-stop="${stop.id}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
        });
        marker.addTo(this.layers);
        this.markers[stop.id] = marker;
        return [stop.lat, stop.lon];
    }

    drawMap() {
        const L = window.L;
        if (!L || !this.map) {
            return;
        }
        this.mapDirty = false;
        this.layers.clearLayers();
        this.markers = {};
        const bounds = [];
        const depot = this.state.depot;
        if (depot) {
            L.marker([depot.lat, depot.lon], { icon: this.pin('<i class="fa fa-home"></i>', "#111827"), title: depot.name })
                .bindPopup(depot.name)
                .addTo(this.layers);
            bounds.push([depot.lat, depot.lon]);
        }
        for (const route of this.state.routes) {
            const color = this.color(route);
            const path = depot ? [[depot.lat, depot.lon]] : [];
            route.stops.forEach((stop, index) => {
                const point = this.addStopMarker(stop, index + 1, color, route.name);
                if (point) {
                    path.push(point);
                    bounds.push(point);
                }
            });
            if (path.length > 1) {
                L.polyline(path, { color, weight: 3, opacity: 0.7 }).addTo(this.layers);
            }
            if (route.position && !route.finished) {
                L.marker([route.position.lat, route.position.lon], {
                    icon: this.pin('<i class="fa fa-truck"></i>', color, "o_rdb_pin_truck"),
                    title: route.driver || route.name,
                    zIndexOffset: 1000,
                })
                    .bindPopup(`${route.driver || route.name} · ${this.positionAge(route)}`)
                    .addTo(this.layers);
            }
        }
        for (const stop of this.state.unassigned) {
            const point = this.addStopMarker(stop, "?", UNASSIGNED_COLOR, "Sin asignar");
            if (point) {
                bounds.push(point);
            }
        }
        // Se encuadra una vez por día: si no, cada refresco le movería el mapa al despachante.
        if (bounds.length && this.fitted !== this.state.day) {
            this.map.invalidateSize();
            this.map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
            this.fitted = this.state.day;
        }
    }
}

registry.category("actions").add("route_dispatch_board", RouteDispatchBoard);
