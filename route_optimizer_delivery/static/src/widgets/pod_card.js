/** @odoo-module **/
import { Component, onWillStart, onWillUpdateProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { hourLabel, parseServerDate } from "../app/delivery_app";
import { reactive, widgetProps } from "../app/owl_compat";
import { lateLabel } from "../board/board_view";

const STEPS = ["scheduled", "on_the_way", "next", "delivered"];

/**
 * Tarjeta de entrega en el formulario del traslado: en qué punto está, la prueba de
 * entrega (quién recibió, fotos, firma) y el link de seguimiento del cliente.
 */
export class RouteDeliveryPodCard extends Component {
    static template = "route_optimizer_delivery.PodCard";

    late(minutes) {
        return lateLabel(minutes);
    }

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.wprops = widgetProps(this);
        this.state = reactive({ info: null });
        onWillStart(() => this.load());
        if (onWillUpdateProps) {
            onWillUpdateProps(() => this.load());
        }
    }

    get record() {
        return this.wprops.record;
    }

    get data() {
        return this.record.data;
    }

    async load() {
        const id = this.record.resId;
        this.state.info = id ? await this.orm.call("stock.picking", "route_delivery_pod_data", [[id]]) : null;
    }

    get step() {
        return this.state.info ? this.state.info.step : "scheduled";
    }

    get title() {
        const info = this.state.info;
        if (!info || !info.in_route) {
            return "Todavía sin ruta";
        }
        return {
            scheduled: "Programada",
            on_the_way: "En camino",
            next: "Es la próxima parada",
            delivered: this.data.route_delivery_state === "partial" ? "Entrega parcial" : "Entregada",
            failed: "No entregada",
            cancel: "Cancelada",
        }[this.step];
    }

    get subtitle() {
        const info = this.state.info;
        if (!info || !info.in_route) {
            return "Sumala a una ruta desde el tablero de despacho.";
        }
        const parts = [info.route];
        if (info.driver) {
            parts.push(info.driver);
        }
        if (this.step === "on_the_way") {
            parts.push(info.ahead === 1 ? "falta 1 parada antes" : `faltan ${info.ahead} paradas antes`);
        }
        return parts.join(" · ");
    }

    get tone() {
        return { delivered: "ok", failed: "bad", cancel: "bad", next: "live", on_the_way: "live" }[this.step] || "idle";
    }

    /** Cuántos tramos de la barra de avance van pintados (de 4). */
    get progress() {
        const index = STEPS.indexOf(this.step);
        return index < 0 ? 0 : index + 1;
    }

    get stepMarks() {
        return STEPS.map((_, index) => index < this.progress);
    }

    asServerString(value) {
        // Los campos fecha llegan como objetos luxon (UTC) en el registro del formulario.
        return value && value.toUTC ? value.toUTC().toFormat("yyyy-MM-dd HH:mm:ss") : value;
    }

    time(value) {
        return hourLabel(this.asServerString(value));
    }

    day(value) {
        const date = parseServerDate(this.asServerString(value));
        return date ? date.toLocaleDateString("es-AR", { weekday: "short", day: "numeric", month: "short" }) : "";
    }

    get signatureUrl() {
        return `/web/image/stock.picking/${this.record.resId}/route_delivery_signature`;
    }

    photoUrl(photo) {
        return `/web/image/${photo.id}`;
    }

    openPhoto(photo) {
        window.open(this.photoUrl(photo), "_blank");
    }

    async copyLink() {
        const [url] = await this.runUrl("action_route_delivery_open_tracking");
        try {
            await navigator.clipboard.writeText(url);
            this.notification.add("Link de seguimiento copiado.", { type: "success" });
        } catch {
            window.open(url, "_blank");
        }
    }

    async runUrl(method) {
        const action = await this.orm.call("stock.picking", method, [[this.record.resId]]);
        return [action.url];
    }

    async openLink() {
        const [url] = await this.runUrl("action_route_delivery_open_tracking");
        window.open(url, "_blank");
    }

    async whatsapp() {
        const [url] = await this.runUrl("action_route_delivery_whatsapp");
        window.open(url, "_blank");
    }

    openRoute() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.picking.batch",
            res_id: this.state.info.route_id,
            views: [[false, "form"]],
        });
    }

    async reset() {
        await this.orm.call("stock.picking", "action_route_delivery_reset", [[this.record.resId]]);
        await this.record.load();
        await this.load();
    }
}

registry.category("view_widgets").add("route_delivery_pod", {
    component: RouteDeliveryPodCard,
    fieldDependencies: [
        { name: "route_delivery_state", type: "selection" },
        { name: "route_delivery_eta", type: "datetime" },
        { name: "route_delivery_done_at", type: "datetime" },
        { name: "route_delivery_user_id", type: "many2one", relation: "res.users" },
        { name: "route_delivery_fail_reason_id", type: "many2one", relation: "route.delivery.fail.reason" },
        { name: "route_delivery_receiver_name", type: "char" },
        { name: "route_delivery_receiver_doc", type: "char" },
        { name: "route_delivery_packages", type: "integer" },
        { name: "route_delivery_cod_amount", type: "float" },
        { name: "route_delivery_partial_detail", type: "text" },
        { name: "route_delivery_note", type: "text" },
    ],
});
