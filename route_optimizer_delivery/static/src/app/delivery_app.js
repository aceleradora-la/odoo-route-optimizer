/** @odoo-module **/
import { Component, onWillStart, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { parseNum, formatNum, normalizeNum, selectOnFocus } from "./number_input";
import { barcodeApi, reactive } from "./owl_compat";
import { offlineStore } from "./offline_store";

const MODEL = "stock.picking.batch";
const PING_MS = 60000;
const SYNC_MS = 20000;
const MAX_PHOTOS = 6;
const PHOTO_MAX_SIDE = 1280;

/** "2026-11-22 14:05:00" (UTC) → Date. */
export function parseServerDate(value) {
    return value ? new Date(value.replace(" ", "T") + "Z") : null;
}

export function hourLabel(value) {
    const date = parseServerDate(value);
    return date ? date.toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
}

/** Achica la foto antes de subirla: las cámaras dan 4–12 MB y en la calle hay 3G. */
function shrinkPhoto(file) {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
            const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(img.width, img.height));
            const canvas = document.createElement("canvas");
            canvas.width = Math.round(img.width * scale);
            canvas.height = Math.round(img.height * scale);
            canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
            URL.revokeObjectURL(url);
            resolve(canvas.toDataURL("image/jpeg", 0.72));
        };
        img.onerror = () => {
            URL.revokeObjectURL(url);
            reject(new Error("No se pudo leer la foto"));
        };
        img.src = url;
    });
}

/** Un pedido falló sin respuesta del servidor (sin señal), no por una regla de negocio. */
function isNetworkError(error) {
    return !(error && (error.data || error.exceptionName));
}

function serverNow() {
    return new Date().toISOString().slice(0, 19).replace("T", " ");
}

function newClientId() {
    return window.crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * App de reparto, pensada para el celular: mis rutas → paradas en orden → una parada.
 * La parada se abre tocándola o escaneando el remito. Todo lo que es número abre el
 * teclado numérico; nombre y comentario, el de texto.
 */
export class RouteDeliveryApp extends Component {
    static template = "route_optimizer_delivery.RouteDeliveryApp";

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        // La app instalable por separado existe desde Odoo 18; antes no hay servicio.
        try {
            this.pwa = useService("pwa");
        } catch {
            this.pwa = null;
        }
        this.state = reactive({
            screen: "home", // home | route | stop | fail
            user: "",
            dispatcher: false,
            routes: [],
            route: null,
            stopId: null,
            busy: false,
            showManual: false,
            manualCode: "",
            form: this.emptyForm(),
            fail: { reasonId: null, note: "", photos: [] },
            gps: "wait", // wait | on | off: si el celular está informando la ubicación
            online: navigator.onLine !== false,
            queue: [], // acciones hechas sin conexión que faltan mandar
            syncing: false,
            confirmPartial: false,
            stale: false, // se está viendo una copia guardada, no datos recién traídos
            checked: {}, // códigos de bultos y productos ya controlados
        });
        this.barcode = barcodeApi();
        this.scannerSupported = this.barcode.isSupported();
        this.position = null;
        this.selectOnFocus = selectOnFocus;
        onWillStart(async () => {
            this.state.queue = await offlineStore.queueAll();
            await this.loadRoutes();
            this.sync();
        });
        this.onOnline = () => {
            this.state.online = true;
            this.sync();
        };
        this.onOffline = () => {
            this.state.online = false;
        };
        window.addEventListener("online", this.onOnline);
        window.addEventListener("offline", this.onOffline);
        this.syncTimer = setInterval(() => this.sync(), SYNC_MS);
        onWillUnmount(() => {
            this.stopTracking();
            clearInterval(this.syncTimer);
            window.removeEventListener("online", this.onOnline);
            window.removeEventListener("offline", this.onOffline);
        });
    }

    emptyForm() {
        return { qty: {}, receiverName: "", receiverDoc: "", packages: "", cod: "", note: "", photos: [], signature: null };
    }

    // ------------------------------------------------------------------
    // Servidor
    // ------------------------------------------------------------------
    async call(method, args = []) {
        this.state.busy = true;
        try {
            // `silent`: sin señal no hace falta el cartel de Odoo; la app avisa a su modo.
            const result = await this.orm.silent.call(MODEL, method, args);
            this.state.online = true;
            return result;
        } catch (error) {
            if (isNetworkError(error)) {
                this.state.online = false;
            } else {
                this.notification.add((error.data && error.data.message) || error.message || String(error), { type: "danger" });
            }
            throw error;
        } finally {
            this.state.busy = false;
        }
    }

    // ------------------------------------------------------------------
    // Sin conexión: cola de acciones y copia de las rutas
    // ------------------------------------------------------------------
    get pendingSync() {
        return this.state.queue.filter((item) => !item.error);
    }

    get failedSync() {
        return this.state.queue.filter((item) => item.error);
    }

    isUnsynced(stopId) {
        return this.state.queue.some((item) => item.stopId === stopId);
    }

    /**
     * Hace una acción del chofer. Con señal va directo al servidor; sin señal se guarda en
     * el teléfono y se aplica en pantalla con `apply`, para que pueda seguir repartiendo.
     * Devuelve true si llegó al servidor, false si quedó guardada.
     */
    async act(item, apply) {
        try {
            this.setRoute(await this.call(item.method, item.args), true);
            return true;
        } catch (error) {
            if (!isNetworkError(error)) {
                throw error;
            }
            item.id = await offlineStore.queueAdd(item);
            this.state.queue.push(item);
            apply(this.state.route);
            this.recount(this.state.route);
            offlineStore.cacheSet("route:" + this.state.route.id, this.state.route);
            return false;
        }
    }

    /** Cuenta de nuevo el avance de una ruta modificada en el teléfono. */
    recount(route) {
        const done = route.stops.filter((stop) => stop.state === "delivered" || stop.state === "partial").length;
        const failed = route.stops.filter((stop) => stop.state === "failed").length;
        route.done_count = done;
        route.failed_count = failed;
        route.pending_count = route.stops.length - done - failed;
        route.progress = route.stops.length ? Math.round((100 * (done + failed)) / route.stops.length) : 0;
        route.finished = route.started && route.pending_count === 0;
        const next = route.stops.find((stop) => stop.state === "pending");
        route.next_stop = next ? next.partner : "";
    }

    /** Aplica sobre una ruta recién traída lo que todavía está en la cola. */
    applyQueue(route) {
        for (const item of this.state.queue) {
            if (item.routeId === route.id && item.patch) {
                if (item.stopId) {
                    const stop = route.stops.find((entry) => entry.id === item.stopId);
                    if (stop) {
                        Object.assign(stop, item.patch);
                    }
                } else {
                    Object.assign(route, item.patch);
                }
            }
        }
        if (this.state.queue.some((item) => item.routeId === route.id)) {
            this.recount(route);
        }
        return route;
    }

    /** Manda al servidor lo guardado, en orden. Corta al primer pedido sin señal. */
    async sync() {
        if (this.state.syncing || !this.pendingSync.length) {
            return;
        }
        this.state.syncing = true;
        let sent = 0;
        try {
            for (const item of [...this.pendingSync]) {
                try {
                    const route = await this.orm.silent.call(MODEL, item.method, item.args);
                    await offlineStore.queueDelete(item.id);
                    this.state.queue = this.state.queue.filter((entry) => entry.id !== item.id);
                    this.state.online = true;
                    sent += 1;
                    if (this.state.route && route && route.id === this.state.route.id) {
                        this.setRoute(route, true);
                    }
                } catch (error) {
                    if (isNetworkError(error)) {
                        this.state.online = false;
                        break;
                    }
                    // El servidor la rechazó (por ejemplo, el depósito sacó la parada de la
                    // ruta): queda a la vista para que el chofer decida, y se sigue con el resto.
                    item.error = (error.data && error.data.message) || error.message || "No se pudo guardar.";
                    await offlineStore.queuePut(item);
                    this.state.queue = [...this.state.queue];
                }
            }
        } finally {
            this.state.syncing = false;
        }
        if (sent) {
            this.notification.add(sent === 1 ? "Se envió 1 acción guardada." : `Se enviaron ${sent} acciones guardadas.`, { type: "success" });
            if (this.state.screen === "home") {
                this.loadRoutes();
            }
        }
    }

    async discardSync(item) {
        await offlineStore.queueDelete(item.id);
        this.state.queue = this.state.queue.filter((entry) => entry.id !== item.id);
        if (this.state.route && this.state.route.id === item.routeId) {
            try {
                this.setRoute(await this.call("route_delivery_app_route", [item.routeId]), true);
            } catch {
                // sin señal se sigue con lo que hay en pantalla
            }
        }
    }

    // ------------------------------------------------------------------
    // Posición del chofer
    // ------------------------------------------------------------------
    startTracking() {
        if (this.watchId !== undefined || !navigator.geolocation) {
            return;
        }
        this.watchId = navigator.geolocation.watchPosition(
            (pos) => {
                const first = !this.position;
                this.position = { lat: pos.coords.latitude, lon: pos.coords.longitude };
                this.state.gps = "on";
                if (first) {
                    this.ping();
                }
            },
            () => {
                this.state.gps = "off";
            },
            { enableHighAccuracy: true, maximumAge: 30000, timeout: 20000 }
        );
        this.pingTimer = setInterval(() => this.ping(), PING_MS);
        this.keepAwake();
        this.onVisible = () => document.visibilityState === "visible" && this.keepAwake();
        document.addEventListener("visibilitychange", this.onVisible);
    }

    /** Pantalla encendida mientras se reparte: con la pantalla apagada el navegador deja
     * de informar la ubicación. El sistema suelta el bloqueo al salir de la app, por eso
     * se vuelve a pedir cada vez que la app vuelve a estar a la vista. */
    async keepAwake() {
        try {
            if (navigator.wakeLock && this.watchId !== undefined) {
                this.wakeLock = await navigator.wakeLock.request("screen");
            }
        } catch {
            // batería baja o navegador sin soporte: se sigue sin bloqueo
        }
    }

    stopTracking() {
        if (this.watchId !== undefined) {
            navigator.geolocation.clearWatch(this.watchId);
            this.watchId = undefined;
        }
        clearInterval(this.pingTimer);
        this.pingTimer = null;
        document.removeEventListener("visibilitychange", this.onVisible);
        this.wakeLock?.release?.().catch(() => {});
        this.wakeLock = null;
    }

    /** Silencioso: sin señal no tiene que molestar al chofer. */
    ping() {
        const route = this.state.route;
        if (route && route.started && !route.finished && this.position) {
            this.orm.silent
                .call(MODEL, "route_delivery_app_ping", [route.id, this.position.lat, this.position.lon])
                .catch(() => {});
        }
    }

    get positionArgs() {
        return this.position ? { lat: this.position.lat, lon: this.position.lon } : {};
    }

    // ------------------------------------------------------------------
    // Inicio: mis rutas
    // ------------------------------------------------------------------
    get todayLabel() {
        const label = new Date().toLocaleDateString("es-AR", { weekday: "long", day: "numeric", month: "long" });
        return label.charAt(0).toUpperCase() + label.slice(1);
    }

    get openRoutes() {
        return this.state.routes.filter((route) => !route.finished);
    }

    get finishedRoutes() {
        return this.state.routes.filter((route) => route.finished);
    }

    dayLabel(value) {
        const date = parseServerDate(value);
        if (!date) {
            return "";
        }
        const weekday = date.toLocaleDateString("es-AR", { weekday: "short" }).replace(".", "");
        return `${weekday} ${date.getDate()}/${date.getMonth() + 1}`;
    }

    hour(value) {
        return hourLabel(value);
    }

    /** Número para mostrar: coma decimal, sin ceros de más. */
    num(value) {
        return formatNum(value) || "0";
    }

    routeSubtitle(route) {
        const parts = [this.dayLabel(route.scheduled)];
        if (route.vehicle) {
            parts.push(route.vehicle);
        }
        if (this.state.dispatcher && route.driver) {
            parts.push(route.driver);
        }
        return parts.filter(Boolean).join(" · ");
    }

    get canInstall() {
        return Boolean(this.pwa) && !this.pwa.isScopedApp && !window.matchMedia("(display-mode: standalone)").matches;
    }

    /** La misma página que Odoo usa para instalar Código de Barras, con nuestro nombre. */
    installApp() {
        const url =
            "/scoped_app?app_id=route_optimizer_delivery&path=" +
            encodeURIComponent("scoped_app/reparto") +
            "&app_name=" +
            encodeURIComponent("Reparto");
        window.open(url, "_blank");
    }

    async loadRoutes() {
        let data;
        try {
            data = await this.call("route_delivery_app_routes");
            offlineStore.cacheSet("routes", data);
            this.state.stale = false;
        } catch (error) {
            data = isNetworkError(error) && (await offlineStore.cacheGet("routes"));
            if (!data) {
                throw error;
            }
            this.state.stale = true;
        }
        this.state.routes = data.routes;
        this.state.user = data.user;
        this.state.dispatcher = data.dispatcher;
    }

    async goHome() {
        this.stopTracking();
        await this.loadRoutes();
        this.state.screen = "home";
        this.state.route = null;
        this.state.stopId = null;
    }

    // ------------------------------------------------------------------
    // Ruta
    // ------------------------------------------------------------------
    /** `fresh`: la ruta viene del servidor; se guarda copia y se le aplica la cola. */
    setRoute(route, fresh = false) {
        if (fresh) {
            this.applyQueue(route);
            offlineStore.cacheSet("route:" + route.id, route);
        }
        this.state.route = route;
        if (route.started && !route.finished) {
            this.startTracking();
        }
    }

    async openRoute(routeId) {
        try {
            this.setRoute(await this.call("route_delivery_app_route", [routeId]), true);
        } catch (error) {
            const cached = isNetworkError(error) && (await offlineStore.cacheGet("route:" + routeId));
            if (!cached) {
                if (isNetworkError(error)) {
                    this.notification.add("Sin conexión, y esta ruta no quedó guardada en el teléfono.", { type: "warning" });
                    return;
                }
                throw error;
            }
            this.setRoute(cached);
        }
        this.state.stopId = null;
        this.state.showManual = false;
        this.state.screen = "route";
    }

    backToRoute() {
        this.state.stopId = null;
        this.state.screen = "route";
    }

    get pendingStops() {
        return this.state.route.stops.filter((stop) => stop.state === "pending");
    }

    get nextStop() {
        return this.pendingStops[0];
    }

    stopNumber(stop) {
        return this.state.route.stops.indexOf(stop) + 1;
    }

    stateLabel(stop) {
        return { delivered: "Entregado", partial: "Parcial", failed: "No entregado" }[stop.state] || "";
    }

    async startRoute() {
        const pos = this.positionArgs;
        const route = this.state.route;
        await this.act(
            { method: "route_delivery_app_start", args: [route.id, pos.lat, pos.lon], routeId: route.id, label: "Salida de " + route.name, patch: { started: true } },
            (local) => {
                local.started = true;
            }
        );
        this.startTracking();
    }

    openMaps(url) {
        if (url) {
            window.open(url, "_blank");
        }
    }

    // ------------------------------------------------------------------
    // Escaneo
    // ------------------------------------------------------------------
    /**
     * Abre la cámara y devuelve el código leído, o null si se cerró sin leer nada.
     * No se usa scanBarcode() de Odoo: su promesa queda colgada cuando se cierra el
     * diálogo o la cámara falla, y acá hay que poder seguir (escribir el código).
     */
    readCode() {
        return new Promise((resolve) => {
            let code = null;
            this.dialog.add(
                this.barcode.BarcodeDialog,
                {
                    facingMode: "environment",
                    onResult: (result) => {
                        code = result;
                    },
                    onError: () => {},
                },
                { onClose: () => resolve(code) }
            );
        });
    }

    toggleManual() {
        this.state.showManual = !this.state.showManual;
    }

    async scan() {
        const code = await this.readCode();
        if (code) {
            await this.openCode(code);
        }
    }

    async submitManual(ev) {
        ev.preventDefault();
        const code = this.state.manualCode.trim();
        if (code) {
            await this.openCode(code);
        }
    }

    /** Abre lo que corresponda al código: una parada de la ruta abierta, o desde el
     * inicio la ruta o parada a la que pertenece. */
    async openCode(code) {
        const routeId = this.state.screen === "home" ? null : this.state.route.id;
        const result = await this.call("route_delivery_app_scan", [code, routeId]);
        if (result.error) {
            this.notification.add(result.error, { type: "warning" });
            return;
        }
        this.state.manualCode = "";
        this.state.showManual = false;
        if (!this.state.route || this.state.route.id !== result.batch_id) {
            this.setRoute(await this.call("route_delivery_app_route", [result.batch_id]));
            this.state.screen = "route";
        }
        if (result.picking_id) {
            this.openStop(result.picking_id);
        }
    }

    // ------------------------------------------------------------------
    // Parada
    // ------------------------------------------------------------------
    // No se llama "stop": las plantillas OWL escriben las variables de t-foreach sobre el
    // componente, y un getter con el mismo nombre que la variable del bucle la pisa.
    get currentStop() {
        const route = this.state.route;
        return route && route.stops.find((stop) => stop.id === this.state.stopId);
    }

    openStop(stopId) {
        const stop = this.state.route.stops.find((item) => item.id === stopId);
        if (!stop) {
            return;
        }
        const form = this.emptyForm();
        for (const line of stop.lines) {
            form.qty[line.id] = formatNum(line.qty) || "0";
        }
        form.packages = stop.packages.length ? String(stop.packages.length) : "";
        this.state.form = form;
        this.state.confirmPartial = false;
        this.state.checked = {};
        this.state.stopId = stopId;
        this.state.screen = "stop";
        document.querySelector(".o_route_delivery_app")?.scrollTo(0, 0);
    }

    lineDelivered(line) {
        return parseNum(this.state.form.qty[line.id]);
    }

    lineShort(line) {
        return this.lineDelivered(line) < line.qty - 0.0001;
    }

    get isPartial() {
        return this.currentStop.lines.some((line) => this.lineShort(line));
    }

    onQtyBlur(line, ev) {
        // Tope en lo pedido y texto prolijo ("3.50" → "3,5"); vacío vuelve a 0.
        const text = formatNum(Math.min(parseNum(ev.target.value), line.qty)) || "0";
        this.state.form.qty[line.id] = text;
        this.state.confirmPartial = false;
        // También directo en el campo: si el estado vuelve al valor ya dibujado, OWL no
        // toca el DOM y quedaría a la vista lo tipeado.
        ev.target.value = text;
    }

    setQty(line, value) {
        this.state.form.qty[line.id] = formatNum(value) || "0";
    }

    onDecimalBlur(field, ev) {
        const text = normalizeNum(ev.target.value);
        this.state.form[field] = text;
        ev.target.value = text;
    }

    onDigitsInput(field, ev) {
        // DNI y bultos: solo dígitos aunque se pegue texto.
        const clean = ev.target.value.replace(/\D/g, "");
        this.state.form[field] = clean;
        ev.target.value = clean;
    }

    // Control por escaneo: bultos del traslado y productos con código de barras.
    get scanTargets() {
        const stop = this.currentStop;
        return [...stop.packages, ...stop.lines.map((line) => line.barcode).filter(Boolean)];
    }

    get checkedCount() {
        return this.scanTargets.filter((code) => this.state.checked[code]).length;
    }

    async scanItem() {
        const code = await this.readCode();
        if (!code) {
            return;
        }
        const target = this.scanTargets.find((item) => item.toLowerCase() === code.trim().toLowerCase());
        if (!target) {
            this.notification.add(`«${code}» no es de esta entrega.`, { type: "danger" });
            return;
        }
        if (this.state.checked[target]) {
            this.notification.add("Ese ya estaba controlado.", { type: "info" });
            return;
        }
        this.state.checked[target] = true;
        const left = this.scanTargets.length - this.checkedCount;
        this.notification.add(left ? `Bien. Faltan ${left}.` : "Todo controlado.", { type: "success" });
    }

    // Fotos (entrega y no entrega comparten la lógica).
    async addPhoto(target, ev) {
        const files = [...ev.target.files];
        ev.target.value = "";
        for (const file of files) {
            if (target.photos.length >= MAX_PHOTOS) {
                this.notification.add(`Hasta ${MAX_PHOTOS} fotos por parada.`, { type: "warning" });
                break;
            }
            try {
                target.photos.push(await shrinkPhoto(file));
            } catch (error) {
                this.notification.add(error.message, { type: "danger" });
            }
        }
    }

    removePhoto(target, index) {
        target.photos.splice(index, 1);
    }

    /** Valor de un campo de texto al estado (reemplaza a t-model, que cambió en OWL 3). */
    setValue(target, key, ev) {
        target[key] = ev.target.value;
    }

    // Firma con el dedo sobre un <canvas>.
    signaturePoint(ev) {
        const rect = ev.currentTarget.getBoundingClientRect();
        return [ev.clientX - rect.left, ev.clientY - rect.top];
    }

    onSignatureDown(ev) {
        ev.preventDefault();
        const canvas = ev.currentTarget;
        canvas.setPointerCapture?.(ev.pointerId);
        const ctx = canvas.getContext("2d");
        if (!this.state.form.signature && !this.signing) {
            // Primer trazo: el lienzo se dimensiona en píxeles reales para que no salga pixelado.
            const ratio = Math.min(window.devicePixelRatio || 1, 2);
            const rect = canvas.getBoundingClientRect();
            canvas.width = Math.max(1, Math.round(rect.width * ratio));
            canvas.height = Math.max(1, Math.round(rect.height * ratio));
            ctx.scale(ratio, ratio);
            ctx.lineWidth = 2.2;
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            ctx.strokeStyle = "#111827";
        }
        this.signing = true;
        const [x, y] = this.signaturePoint(ev);
        ctx.beginPath();
        ctx.moveTo(x, y);
        // Un toque sin arrastre también deja marca.
        ctx.lineTo(x + 0.1, y + 0.1);
        ctx.stroke();
    }

    onSignatureMove(ev) {
        if (!this.signing) {
            return;
        }
        ev.preventDefault();
        const [x, y] = this.signaturePoint(ev);
        const ctx = ev.currentTarget.getContext("2d");
        ctx.lineTo(x, y);
        ctx.stroke();
    }

    onSignatureUp(ev) {
        if (!this.signing) {
            return;
        }
        this.signing = false;
        this.state.form.signature = ev.currentTarget.toDataURL("image/png");
    }

    clearSignature() {
        const canvas = document.querySelector(".o_route_delivery_app .o_rd_signature_canvas");
        if (canvas) {
            canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
        }
        this.state.form.signature = null;
    }

    async deliver() {
        const stop = this.currentStop;
        const form = this.state.form;
        const route = this.state.route;
        if (route.require_signature && !form.signature) {
            this.notification.add("Falta la firma de quien recibe.", { type: "warning" });
            return;
        }
        if (route.require_photo && !form.photos.length) {
            this.notification.add("Falta la foto de la entrega.", { type: "warning" });
            return;
        }
        const vals = {
            lines: stop.lines.map((line) => ({ id: line.id, qty: this.lineDelivered(line) })),
            receiver_name: form.receiverName,
            receiver_doc: form.receiverDoc,
            packages: parseNum(form.packages),
            cod_amount: parseNum(form.cod),
            note: form.note,
            photos: [...form.photos],
            signature: form.signature,
            ...this.positionArgs,
        };
        vals.client_id = newClientId();
        vals.client_time = serverNow();
        const patch = {
            state: this.isPartial ? "partial" : "delivered",
            done_at: vals.client_time,
            receiver_name: vals.receiver_name,
            receiver_doc: vals.receiver_doc,
            delivered_packages: vals.packages,
            cod_amount: vals.cod_amount,
            driver_note: vals.note,
            photo_count: vals.photos.length,
            has_signature: Boolean(vals.signature),
        };
        const send = async () => {
            const sent = await this.act(
                { method: "route_delivery_app_deliver", args: [stop.id, vals], routeId: this.state.route.id, stopId: stop.id, label: stop.partner, patch },
                (route) => Object.assign(route.stops.find((entry) => entry.id === stop.id), patch)
            );
            this.notification.add(
                sent ? `${stop.partner}: entregado.` : `${stop.partner}: guardado en el teléfono. Se envía cuando vuelva la señal.`,
                { type: sent ? "success" : "info" }
            );
            this.afterStop();
        };
        // Entrega parcial: se confirma con un segundo toque en la misma pantalla. No se usa
        // un diálogo porque sin conexión Odoo 20 deshabilita los botones de los diálogos.
        if (this.isPartial && !this.state.confirmPartial) {
            this.state.confirmPartial = true;
            return;
        }
        this.state.confirmPartial = false;
        await send();
    }

    get partialSummary() {
        return this.currentStop.lines
            .filter((line) => this.lineShort(line))
            .map((line) => `${line.product}: ${formatNum(this.lineDelivered(line)) || "0"} de ${formatNum(line.qty)}`)
            .join(" · ");
    }

    /** Tras cerrar una parada se vuelve a la lista, que ya muestra la próxima arriba. */
    afterStop() {
        this.backToRoute();
        if (this.state.route.finished) {
            this.stopTracking();
            this.notification.add("Ruta terminada.", { type: "success" });
        }
    }

    openFail() {
        this.state.fail = { reasonId: null, note: "", photos: [] };
        this.state.screen = "fail";
    }

    backToStop() {
        this.state.screen = "stop";
    }

    get failReason() {
        return this.state.route.reasons.find((reason) => reason.id === this.state.fail.reasonId);
    }

    async confirmFail() {
        const stop = this.currentStop;
        const fail = this.state.fail;
        if (!fail.reasonId) {
            this.notification.add("Elegí el motivo.", { type: "warning" });
            return;
        }
        if (this.failReason.require_note && !fail.note.trim()) {
            this.notification.add("Contá qué pasó: este motivo pide una aclaración.", { type: "warning" });
            return;
        }
        const vals = {
            reason_id: fail.reasonId,
            note: fail.note,
            photos: [...fail.photos],
            client_id: newClientId(),
            client_time: serverNow(),
            ...this.positionArgs,
        };
        const patch = { state: "failed", done_at: vals.client_time, fail_reason: this.failReason.name, driver_note: fail.note };
        const sent = await this.act(
            { method: "route_delivery_app_fail", args: [stop.id, vals], routeId: this.state.route.id, stopId: stop.id, label: stop.partner, patch },
            (route) => Object.assign(route.stops.find((entry) => entry.id === stop.id), patch)
        );
        this.notification.add(
            sent ? `${stop.partner}: no entregado.` : `${stop.partner}: guardado en el teléfono. Se envía cuando vuelva la señal.`,
            { type: sent ? "warning" : "info" }
        );
        this.afterStop();
    }

    reopen() {
        const stop = this.currentStop;
        this.dialog.add(ConfirmationDialog, {
            title: "Corregir parada",
            body: "La parada vuelve a Pendiente para cargarla de nuevo.",
            confirmLabel: "Corregir",
            cancelLabel: "Dejar como está",
            confirm: async () => {
                try {
                    this.setRoute(await this.call("route_delivery_app_reopen", [stop.id]), true);
                } catch (error) {
                    if (isNetworkError(error)) {
                        this.notification.add("Para corregir una parada hace falta conexión.", { type: "warning" });
                        return;
                    }
                    throw error;
                }
                this.openStop(stop.id);
            },
            cancel: () => {},
        });
    }
}

registry.category("actions").add("route_delivery_app", RouteDeliveryApp);
