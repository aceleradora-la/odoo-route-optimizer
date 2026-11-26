/** @odoo-module **/
import { Component, onWillStart, onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { hourLabel, parseServerDate } from "../app/delivery_app";
import { reactive } from "../app/owl_compat";
import { createMap, decodePolyline } from "./map_adapter";
import { lateLabel, recallBoardView, rememberBoardView } from "./board_view";

const MODEL = "stock.picking.batch";
const REFRESH_MS = 30000;
// Colores separados también en claridad, para distinguir rutas sin depender del tono.
const ROUTE_COLORS = ["#1d4ed8", "#ea580c", "#0f766e", "#7c3aed", "#be185d", "#4d7c0f", "#0369a1", "#b45309"];
const UNASSIGNED_COLOR = "#4b5563";
const STOP_MINUTES = 20; // ancho de una parada en la línea de tiempo

function isoDay(date) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function pointInPolygon(x, y, polygon) {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
        const [xi, yi] = polygon[i];
        const [xj, yj] = polygon[j];
        if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
            inside = !inside;
        }
    }
    return inside;
}

/**
 * Despacho: una sola pantalla con el mapa al centro, las entregas sin asignar a la
 * izquierda y las rutas a la derecha.
 *
 * Planificar: se eligen entregas (lista, clic en el mapa o lazo) y el panel derecho
 * ordena las rutas por lo que cuesta sumarlas. Todo se puede arrastrar: entregas a una
 * ruta, paradas para reordenar, paradas afuera para sacarlas.
 * En vivo: camiones, atrasos, incidencias y una línea de tiempo por ruta.
 */
export class RouteDispatchBoard extends Component {
    static template = "route_optimizer_delivery.RouteDispatchBoard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        // Si se vuelve de abrir una ruta o un traslado, se retoma donde se estaba.
        const last = recallBoardView() || {};
        this.state = reactive({
            day: last.day || isoDay(new Date()),
            mode: last.mode === "live" ? "live" : "plan", // plan | live
            tool: "move", // move | lasso
            routes: [],
            unassigned: [],
            depot: false,
            drivers: [],
            routing: "estimate",
            loading: true,
            busy: false,
            selection: {},
            expanded: last.expanded || null,
            search: last.search || "",
            zone: last.zone || "",
            options: [],
            optionsLoading: false,
            drop: null,
            traffic: false,
            lasso: "",
            mapProvider: "osm",
            now: Date.now(),
        });
        this.drag = null;
        this.mapConfig = null;
        this.map = null;
        this.mapDirty = false;
        this.fitted = null;
        this.optionsToken = 0;
        onWillStart(() => this.load());
        onMounted(async () => {
            this.mapEl = document.querySelector(".o_route_dispatch_board .o_rdb_map");
            const map = await createMap(this.mapEl, this.mapConfig);
            if (this.gone) {
                // Se salió del tablero mientras cargaba el mapa.
                map.destroy();
                return;
            }
            this.map = map;
            this.state.mapProvider = this.map.supportsTraffic ? "google" : "osm";
            this.map.onPinClick((id) => this.toggle(id));
            this.drawMap();
            this.timer = setInterval(() => {
                this.state.now = Date.now();
                if (!this.drag && !this.state.busy) {
                    this.load();
                }
            }, REFRESH_MS);
        });
        onPatched(() => {
            if (this.mapDirty) {
                this.drawMap();
            }
        });
        onWillUnmount(() => {
            this.gone = true;
            this.remember();
            clearInterval(this.timer);
            if (this.map) {
                this.map.destroy();
            }
        });
    }

    // ------------------------------------------------------------------
    // Datos
    // ------------------------------------------------------------------
    /** Guarda dónde está parado el despachante (ver board_view.js). */
    remember() {
        const { day, mode, expanded, search, zone } = this.state;
        rememberBoardView({ day, mode, expanded, search, zone });
    }

    async load() {
        this.remember();
        const data = await this.orm.call(MODEL, "route_dispatch_data", [this.state.day]);
        this.mapConfig = data.map;
        this.mapDirty = true;
        Object.assign(this.state, {
            routes: data.routes,
            unassigned: data.unassigned,
            depot: data.depot,
            drivers: data.drivers,
            routing: data.routing,
            loading: false,
        });
        // Lo elegido que ya no está pendiente (lo entregó el chofer, lo movió otro) se suelta.
        const pending = new Set(this.allStops.filter((stop) => stop.state === "pending").map((stop) => stop.id));
        for (const id of Object.keys(this.state.selection)) {
            if (!pending.has(Number(id))) {
                delete this.state.selection[id];
            }
        }
    }

    /** Ejecuta una acción del tablero, recarga y avisa si el servidor la rechaza. */
    async run(method, args) {
        this.state.busy = true;
        try {
            return await this.orm.call(MODEL, method, args);
        } finally {
            this.state.busy = false;
            await this.load();
            this.loadOptions();
        }
    }

    setValue(target, key, ev) {
        target[key] = ev.target.value;
    }

    async setDay(day) {
        if (!day) {
            return;
        }
        this.state.day = day;
        this.state.selection = {};
        this.state.options = [];
        this.fitted = null;
        await this.load();
    }

    shiftDay(delta) {
        const date = new Date(this.state.day + "T12:00:00");
        date.setDate(date.getDate() + delta);
        return this.setDay(isoDay(date));
    }

    get isToday() {
        return this.state.day === isoDay(new Date());
    }

    get dayLabel() {
        const label = new Date(this.state.day + "T12:00:00").toLocaleDateString("es-AR", {
            weekday: "long",
            day: "numeric",
            month: "long",
        });
        return label.charAt(0).toUpperCase() + label.slice(1);
    }

    get allStops() {
        return [...this.state.unassigned, ...this.state.routes.flatMap((route) => route.stops)];
    }

    get totals() {
        const sum = (key) => this.state.routes.reduce((acc, route) => acc + route[key], 0);
        return {
            deliveries: sum("stop_count") + this.state.unassigned.length,
            routes: this.state.routes.length,
            done: sum("done_count"),
            failed: sum("failed_count"),
            unassigned: this.state.unassigned.length,
            weight: Math.round(this.allStops.reduce((acc, stop) => acc + stop.weight, 0)),
            late: this.state.routes.filter((route) => route.delay_minutes >= 10 && !route.finished).length,
            drafts: this.state.routes.filter((route) => route.state === "draft" && route.stop_count).length,
        };
    }

    color(route) {
        return ROUTE_COLORS[this.state.routes.indexOf(route) % ROUTE_COLORS.length];
    }

    routeById(id) {
        return this.state.routes.find((route) => route.id === id);
    }

    hour(value) {
        return hourLabel(value);
    }

    kg(value) {
        return Math.round(value || 0).toLocaleString("es-AR");
    }

    duration(minutes) {
        if (!minutes) {
            return "";
        }
        const hours = Math.floor(minutes / 60);
        const rest = minutes % 60;
        return hours ? `${hours} h ${String(rest).padStart(2, "0")}` : `${rest} min`;
    }

    routeTitle(route) {
        return route.driver ? `${route.name} · ${route.driver}` : route.name;
    }

    /** Etiqueta de estado de una ruta: { text, tone }. */
    routeTag(route) {
        if (route.finished) {
            return { text: "Terminada", tone: "ok" };
        }
        if (route.started) {
            return route.delay_minutes >= 10
                ? { text: `${lateLabel(route.delay_minutes)} tarde`, tone: "warn" }
                : { text: "En horario", tone: "ok" };
        }
        if (route.capacity && route.weight > route.capacity) {
            return { text: "Excede la carga", tone: "bad" };
        }
        if (!route.driver) {
            return { text: "Sin chofer", tone: "warn" };
        }
        return route.state === "draft" ? { text: "Borrador", tone: "idle" } : { text: "Publicada", tone: "ok" };
    }

    loadPct(route) {
        return route.capacity ? Math.min(100, Math.round((route.weight / route.capacity) * 100)) : 0;
    }

    stateLabel(stop) {
        return { delivered: "Entregado", partial: "Parcial", failed: "No entregado" }[stop.state] || "";
    }

    // ------------------------------------------------------------------
    // Sin asignar: búsqueda y filtros
    // ------------------------------------------------------------------
    get zones() {
        return [...new Set(this.state.unassigned.map((stop) => stop.zone).filter(Boolean))].sort();
    }

    get visibleUnassigned() {
        const text = this.state.search.trim().toLowerCase();
        return this.state.unassigned.filter((stop) => {
            if (this.state.zone && stop.zone !== this.state.zone) {
                return false;
            }
            return (
                !text ||
                [stop.partner, stop.address, stop.name, stop.origin].some((value) =>
                    (value || "").toLowerCase().includes(text)
                )
            );
        });
    }

    setZone(zone) {
        this.state.zone = this.state.zone === zone ? "" : zone;
    }

    // ------------------------------------------------------------------
    // Selección y sugerencias
    // ------------------------------------------------------------------
    get selectedIds() {
        return Object.keys(this.state.selection).map(Number);
    }

    get selectedStops() {
        const ids = new Set(this.selectedIds);
        return this.allStops.filter((stop) => ids.has(stop.id));
    }

    get selectedWeight() {
        return this.selectedStops.reduce((acc, stop) => acc + stop.weight, 0);
    }

    get selectionHasAssigned() {
        return this.selectedStops.some((stop) => stop.batch_id);
    }

    isSelected(id) {
        return Boolean(this.state.selection[id]);
    }

    toggle(id) {
        const stop = this.allStops.find((item) => item.id === id);
        if (!stop) {
            return;
        }
        if (stop.state !== "pending" || this.state.mode === "live") {
            // Lo ya visitado no se mueve: se abre su ruta para verlo en contexto.
            this.state.expanded = stop.batch_id || this.state.expanded;
            return;
        }
        if (this.state.selection[id]) {
            delete this.state.selection[id];
        } else {
            this.state.selection[id] = true;
        }
        this.mapDirty = true;
        this.loadOptions();
    }

    selectAllVisible() {
        for (const stop of this.visibleUnassigned) {
            this.state.selection[stop.id] = true;
        }
        this.mapDirty = true;
        this.loadOptions();
    }

    clearSelection() {
        this.state.selection = {};
        this.state.options = [];
        this.mapDirty = true;
    }

    /** Pide al servidor dónde conviene sumar lo elegido. Solo vale la última respuesta. */
    async loadOptions() {
        const ids = this.selectedIds;
        const token = ++this.optionsToken;
        if (!ids.length) {
            this.state.options = [];
            return;
        }
        this.state.optionsLoading = true;
        try {
            const options = await this.orm.call(MODEL, "route_dispatch_suggest", [ids, this.state.day]);
            if (token === this.optionsToken) {
                this.state.options = options;
            }
        } finally {
            if (token === this.optionsToken) {
                this.state.optionsLoading = false;
            }
        }
    }

    optionColor(option) {
        const route = this.routeById(option.batch_id);
        return route ? this.color(route) : UNASSIGNED_COLOR;
    }

    optionPlace(option) {
        if (option.before && option.after) {
            return `Entre ${option.before} y ${option.after}.`;
        }
        if (option.before) {
            return `Después de ${option.before}, al final.`;
        }
        return option.after ? `Primera, antes de ${option.after}.` : "Primera parada de la ruta.";
    }

    optionLoad(option) {
        return option.capacity
            ? `Carga: ${this.kg(option.load)} / ${this.kg(option.capacity)} kg.`
            : `Carga: ${this.kg(option.load)} kg.`;
    }

    async assign(batchId, ids = this.selectedIds) {
        await this.run("route_dispatch_assign", [ids, batchId]);
        this.state.expanded = batchId;
        this.clearSelection();
    }

    async newRoute(ids = this.selectedIds) {
        const batchId = await this.run("route_dispatch_new_route", [this.state.day, ids]);
        this.state.expanded = batchId;
        this.clearSelection();
    }

    async unassign(ids = this.selectedIds) {
        await this.run("route_dispatch_unassign", [ids]);
        this.clearSelection();
    }

    // ------------------------------------------------------------------
    // Rutas
    // ------------------------------------------------------------------
    toggleRoute(route) {
        this.state.expanded = this.state.expanded === route.id ? null : route.id;
    }

    async setDriver(route, ev) {
        await this.run("route_dispatch_set_driver", [route.id, Number(ev.target.value) || false]);
    }

    async optimize(route) {
        const action = await this.orm.call(MODEL, "action_route_optimizer_wizard", [[route.id]]);
        // Una acción pedida por RPC no pasa por el armado de vistas del servidor.
        action.views = [[false, "form"]];
        this.action.doAction(action, { onClose: () => this.load() });
    }

    async optimizeAll() {
        const ids = this.state.routes.filter((route) => !route.started && route.stop_count > 1).map((route) => route.id);
        if (!ids.length) {
            this.notification.add("No hay rutas sin salir para optimizar.", { type: "info" });
            return;
        }
        const result = await this.run("route_dispatch_optimize", [ids]);
        if (result.optimized.length) {
            this.notification.add(`Optimizadas: ${result.optimized.join(", ")}.`, { type: "success" });
        }
        for (const error of result.errors) {
            this.notification.add(error, { type: "danger", sticky: true });
        }
    }

    async publish() {
        const result = await this.run("route_dispatch_publish", [this.state.day]);
        if (result.published) {
            this.notification.add(
                result.published === 1 ? "1 ruta publicada: ya la ve su chofer." : `${result.published} rutas publicadas: ya las ven sus choferes.`,
                { type: "success" }
            );
        }
        if (result.without_driver.length) {
            this.notification.add(`Sin chofer, no se publicaron: ${result.without_driver.join(", ")}.`, { type: "warning" });
        } else if (!result.published) {
            this.notification.add("No hay rutas en borrador para publicar.", { type: "info" });
        }
    }

    openRoute(route) {
        this.remember();
        this.action.doAction({ type: "ir.actions.act_window", res_model: MODEL, res_id: route.id, views: [[false, "form"]] });
    }

    openStop(stop) {
        this.remember();
        this.action.doAction({ type: "ir.actions.act_window", res_model: "stock.picking", res_id: stop.id, views: [[false, "form"]] });
    }

    focusStop(stop) {
        if (this.map && (stop.lat || stop.lon)) {
            this.map.panTo(stop.lat, stop.lon);
        }
    }

    // ------------------------------------------------------------------
    // Arrastrar y soltar
    // ------------------------------------------------------------------
    onDragStart(stop, ev) {
        if (stop.state !== "pending") {
            ev.preventDefault();
            return;
        }
        // Arrastrar una entrega elegida lleva todo lo elegido.
        const ids = this.isSelected(stop.id) ? this.selectedIds : [stop.id];
        this.drag = { ids, single: ids.length === 1 ? stop.id : null };
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", ids.join(","));
    }

    onDragEnd() {
        this.drag = null;
        this.state.drop = null;
    }

    /** `target`: id de ruta, 0 (sin asignar) o "new". `index`: posición entre las paradas. */
    onDragOver(target, index, ev) {
        if (!this.drag) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        ev.dataTransfer.dropEffect = "move";
        const drop = this.state.drop;
        if (!drop || drop.target !== target || drop.index !== index) {
            this.state.drop = { target, index };
        }
    }

    isDrop(target, index = null) {
        const drop = this.state.drop;
        return Boolean(drop && drop.target === target && drop.index === index);
    }

    /** Índice de la parada dentro de su ruta, sin contar la que se arrastra. */
    stopIndex(route, stop) {
        return route.stops.filter((item) => item.id !== this.drag?.single).indexOf(stop);
    }

    async onDrop(target, index, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const drag = this.drag;
        this.onDragEnd();
        if (!drag) {
            return;
        }
        if (target === "new") {
            await this.newRoute(drag.ids);
        } else if (!target) {
            await this.unassign(drag.ids);
        } else if (drag.single && index !== null) {
            await this.run("route_dispatch_move", [drag.single, target, index]);
        } else {
            await this.assign(target, drag.ids);
        }
    }

    // ------------------------------------------------------------------
    // Lazo
    // ------------------------------------------------------------------
    setTool(tool) {
        this.state.tool = tool;
        this.state.lasso = "";
        if (this.map) {
            this.map.setInteractive(tool !== "lasso");
        }
    }

    lassoPoint(ev) {
        const rect = this.mapEl.getBoundingClientRect();
        return [ev.clientX - rect.left, ev.clientY - rect.top];
    }

    onLassoDown(ev) {
        ev.preventDefault();
        ev.currentTarget.setPointerCapture?.(ev.pointerId);
        this.lassoPath = [this.lassoPoint(ev)];
        this.state.lasso = this.lassoPath[0].join(",");
    }

    onLassoMove(ev) {
        if (!this.lassoPath) {
            return;
        }
        this.lassoPath.push(this.lassoPoint(ev));
        this.state.lasso = this.lassoPath.map((point) => point.join(",")).join(" ");
    }

    onLassoUp() {
        const path = this.lassoPath;
        this.lassoPath = null;
        this.state.lasso = "";
        if (!path || path.length < 3 || !this.map) {
            return;
        }
        let added = 0;
        for (const stop of this.allStops) {
            if (stop.state !== "pending" || (!stop.lat && !stop.lon)) {
                continue;
            }
            const point = this.map.project(stop.lat, stop.lon);
            if (pointInPolygon(point.x, point.y, path) && !this.state.selection[stop.id]) {
                this.state.selection[stop.id] = true;
                added += 1;
            }
        }
        if (added) {
            this.mapDirty = true;
            this.loadOptions();
        } else {
            this.notification.add("El lazo no encerró ninguna entrega pendiente.", { type: "info" });
        }
    }

    toggleTraffic() {
        this.state.traffic = !this.state.traffic;
        this.map.setTraffic(this.state.traffic);
    }

    // ------------------------------------------------------------------
    // En vivo
    // ------------------------------------------------------------------
    setMode(mode) {
        this.state.mode = mode;
        if (mode === "live") {
            this.clearSelection();
            this.setTool("move");
        }
        this.mapDirty = true;
        // El mapa cambia de alto al aparecer la línea de tiempo.
        setTimeout(() => this.map && this.map.resize(), 50);
    }

    get incidents() {
        return this.state.routes.flatMap((route) =>
            route.stops.filter((stop) => stop.state === "failed").map((stop) => ({ stop, route }))
        );
    }

    retry(stop) {
        return this.run("route_dispatch_retry", [stop.id]);
    }

    postpone(stop) {
        this.dialog.add(ConfirmationDialog, {
            title: "Pasar a mañana",
            body: `${stop.partner} sale de la ruta y queda sin asignar para el día siguiente.`,
            confirmLabel: "Pasar a mañana",
            cancelLabel: "Cancelar",
            confirm: () => this.run("route_dispatch_postpone", [stop.id]),
            cancel: () => {},
        });
    }

    nextStop(route) {
        return route.stops.find((stop) => stop.state === "pending");
    }

    positionAge(route) {
        if (!route.position) {
            return "";
        }
        const minutes = Math.max(0, Math.round((this.state.now - parseServerDate(route.position.at)) / 60000));
        return minutes < 2 ? "ubicación de recién" : `sin señal hace ${minutes} min`;
    }

    /** Franja horaria de la línea de tiempo: del primer al último evento, mínimo 8 a 18. */
    get timeline() {
        const times = this.state.routes
            .flatMap((route) => route.stops)
            .map((stop) => parseServerDate(stop.done_at || stop.eta))
            .filter(Boolean);
        const dayStart = new Date(this.state.day + "T00:00:00");
        const hourOf = (date) => (date - dayStart) / 3600000;
        const from = Math.floor(Math.min(8, ...times.map(hourOf)));
        const to = Math.ceil(Math.max(18, ...times.map((date) => hourOf(date) + STOP_MINUTES / 60)));
        const span = to - from;
        const lanes = this.state.routes.map((route) => ({
            route,
            bars: route.stops
                .map((stop) => {
                    const at = parseServerDate(stop.done_at || stop.eta);
                    if (!at) {
                        return null;
                    }
                    let tone = "plan";
                    if (stop.state === "failed") {
                        tone = "fail";
                    } else if (stop.state !== "pending") {
                        tone = "done";
                    } else if (stop.late_minutes >= 10) {
                        tone = "late";
                    }
                    return {
                        stop,
                        tone,
                        left: ((hourOf(at) - from) / span) * 100,
                        width: (STOP_MINUTES / 60 / span) * 100,
                    };
                })
                .filter(Boolean),
        }));
        const now = hourOf(new Date(this.state.now));
        return {
            hours: Array.from({ length: span }, (_, i) => from + i),
            lanes,
            now: this.isToday && now >= from && now <= to ? ((now - from) / span) * 100 : null,
        };
    }

    // ------------------------------------------------------------------
    // Mapa
    // ------------------------------------------------------------------
    drawMap() {
        if (!this.map) {
            return;
        }
        this.mapDirty = false;
        const depot = this.state.depot;
        const live = this.state.mode === "live";
        const lines = [];
        const pins = [];
        const trucks = [];
        const bounds = depot ? [[depot.lat, depot.lon]] : [];
        for (const route of this.state.routes) {
            const color = this.color(route);
            const located = route.stops.filter((stop) => stop.lat || stop.lon);
            const visited = located.filter((stop) => stop.state !== "pending");
            const pending = located.filter((stop) => stop.state === "pending");
            const start = depot ? [[depot.lat, depot.lon]] : [];
            if (visited.length) {
                lines.push({ color, faded: true, path: [...start, ...visited.map((stop) => [stop.lat, stop.lon])] });
            }
            if (route.polylines.length) {
                for (const encoded of route.polylines) {
                    lines.push({ color, path: decodePolyline(encoded) });
                }
            } else if (pending.length) {
                // Sin recorrido por calles se une en línea recta, punteada para que se note.
                const origin = visited.length ? [[visited.at(-1).lat, visited.at(-1).lon]] : start;
                lines.push({ color, dashed: true, path: [...origin, ...pending.map((stop) => [stop.lat, stop.lon])] });
            }
            route.stops.forEach((stop, index) => {
                if (!stop.lat && !stop.lon) {
                    return;
                }
                const done = stop.state !== "pending";
                pins.push({
                    id: stop.id,
                    lat: stop.lat,
                    lon: stop.lon,
                    color,
                    label: done ? (stop.state === "failed" ? "×" : "✓") : index + 1,
                    done,
                    selected: this.isSelected(stop.id),
                    title: `${route.name} · ${index + 1}. ${stop.partner}`,
                });
                bounds.push([stop.lat, stop.lon]);
            });
            if (route.position && route.started && !route.finished) {
                trucks.push({
                    lat: route.position.lat,
                    lon: route.position.lon,
                    color,
                    label: (route.driver || route.name).split(" ")[0],
                    title: `${route.driver || route.name} · ${this.positionAge(route)}`,
                });
            }
        }
        if (!live) {
            for (const stop of this.state.unassigned) {
                if (!stop.lat && !stop.lon) {
                    continue;
                }
                pins.push({
                    id: stop.id,
                    lat: stop.lat,
                    lon: stop.lon,
                    color: UNASSIGNED_COLOR,
                    label: "?",
                    kind: "unassigned",
                    selected: this.isSelected(stop.id),
                    title: `Sin asignar · ${stop.partner}`,
                });
                bounds.push([stop.lat, stop.lon]);
            }
        }
        this.map.render({ depot, lines, pins, trucks });
        // Se encuadra una vez por día: si no, cada refresco le movería el mapa al despachante.
        if (bounds.length && this.fitted !== this.state.day) {
            this.map.fit(bounds);
            this.fitted = this.state.day;
        }
    }
}

registry.category("actions").add("route_dispatch_board", RouteDispatchBoard);
