/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, onWillUpdateProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { hourLabel } from "../app/delivery_app";
import { reactive, widgetProps } from "../app/owl_compat";
import { createMap, decodePolyline } from "../board/map_adapter";
import { lateLabel, localDay, rememberBoardView } from "../board/board_view";

const COLOR = "#1d4ed8";

/**
 * Pestaña «Recorrido» del lote: la ruta en el mapa y sus paradas en orden, con estado y
 * hora estimada. Es de solo lectura; para mover paradas está el tablero de despacho.
 */
export class RouteDeliveryRoutePanel extends Component {
    static template = "route_optimizer_delivery.RoutePanel";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.wprops = widgetProps(this);
        this.state = reactive({ route: null });
        onWillStart(() => this.load());
        if (onWillUpdateProps) {
            onWillUpdateProps(async () => {
                await this.load();
                this.draw();
            });
        }
        onMounted(async () => {
            const el = document.querySelector(".o_rdw_route .o_rdw_map");
            if (!el || !this.state.route) {
                return;
            }
            const map = await createMap(el, this.state.route.map);
            if (this.gone) {
                // Se salió de la pantalla mientras cargaba el mapa.
                map.destroy();
                return;
            }
            this.map = map;
            this.map.onPinClick((id) => this.focus(id));
            this.draw(true);
        });
        onWillUnmount(() => {
            this.gone = true;
            if (this.map) {
                this.map.destroy();
            }
        });
    }

    async load() {
        const id = this.wprops.record.resId;
        this.state.route = id ? await this.orm.call("stock.picking.batch", "route_dispatch_route", [[id]]) : null;
    }

    hour(value) {
        return hourLabel(value);
    }

    stateLabel(stop) {
        return { delivered: "Entregado", partial: "Parcial", failed: "No entregado" }[stop.state] || "";
    }

    get statusLabel() {
        const route = this.state.route;
        if (route.finished) {
            return "Terminada";
        }
        if (route.started) {
            return route.delay_minutes >= 10 ? `En la calle · ${lateLabel(route.delay_minutes)} tarde` : "En la calle · en horario";
        }
        return route.state === "draft" ? "Borrador: el chofer todavía no la ve" : "Publicada";
    }

    duration(minutes) {
        const hours = Math.floor(minutes / 60);
        return hours ? `${hours} h ${String(minutes % 60).padStart(2, "0")}` : `${minutes} min`;
    }

    focus(id) {
        const stop = this.state.route.stops.find((item) => item.id === id);
        if (stop && this.map && (stop.lat || stop.lon)) {
            this.map.panTo(stop.lat, stop.lon);
        }
    }

    openStop(stop) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "stock.picking", res_id: stop.id, views: [[false, "form"]] });
    }

    openMaps() {
        window.open(this.state.route.gmaps_url, "_blank");
    }

    async shareWhatsapp() {
        const action = await this.orm.call("stock.picking.batch", "action_share_whatsapp", [[this.state.route.id]]);
        window.open(action.url, "_blank");
    }

    async refreshEtas() {
        await this.orm.call("stock.picking.batch", "action_route_delivery_update_etas", [[this.state.route.id]]);
        await this.load();
        this.draw();
    }

    /** Abre el tablero en el día de esta ruta, con la ruta desplegada. */
    openBoard() {
        const route = this.state.route;
        rememberBoardView({
            day: localDay(route.scheduled),
            mode: route.started && !route.finished ? "live" : "plan",
            expanded: route.id,
        });
        this.action.doAction("route_optimizer_delivery.action_route_dispatch_board");
    }

    draw(fit = false) {
        const route = this.state.route;
        if (!this.map || !route) {
            return;
        }
        const depot = route.depot;
        const located = route.stops.filter((stop) => stop.lat || stop.lon);
        const visited = located.filter((stop) => stop.state !== "pending");
        const pending = located.filter((stop) => stop.state === "pending");
        const start = depot ? [[depot.lat, depot.lon]] : [];
        const lines = [];
        if (visited.length) {
            lines.push({ color: COLOR, faded: true, path: [...start, ...visited.map((stop) => [stop.lat, stop.lon])] });
        }
        if (route.polylines.length) {
            for (const encoded of route.polylines) {
                lines.push({ color: COLOR, path: decodePolyline(encoded) });
            }
        } else if (pending.length) {
            const origin = visited.length ? [[visited.at(-1).lat, visited.at(-1).lon]] : start;
            lines.push({ color: COLOR, dashed: true, path: [...origin, ...pending.map((stop) => [stop.lat, stop.lon])] });
        }
        const pins = [];
        route.stops.forEach((stop, index) => {
            if (!stop.lat && !stop.lon) {
                return;
            }
            const done = stop.state !== "pending";
            pins.push({
                id: stop.id,
                lat: stop.lat,
                lon: stop.lon,
                color: stop.state === "failed" ? "#b91c1c" : COLOR,
                label: done ? (stop.state === "failed" ? "×" : "✓") : index + 1,
                done,
                title: `${index + 1}. ${stop.partner}`,
            });
        });
        const trucks =
            route.position && route.started && !route.finished
                ? [{ lat: route.position.lat, lon: route.position.lon, color: COLOR, label: (route.driver || "").split(" ")[0], title: route.driver }]
                : [];
        this.map.render({ depot, lines, pins, trucks });
        if (fit) {
            this.map.fit([...start, ...located.map((stop) => [stop.lat, stop.lon])]);
        }
    }
}

registry.category("view_widgets").add("route_delivery_route", { component: RouteDeliveryRoutePanel });
