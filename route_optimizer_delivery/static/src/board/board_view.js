/** @odoo-module **/
/**
 * Dónde estaba parado el despachante en el tablero: día, vista y ruta abierta.
 *
 * Al abrir un traslado o una ruta desde el tablero y volver, Odoo arma el tablero de
 * nuevo; sin esto volvía siempre al día de hoy. Se guarda por pestaña del navegador y
 * vale solo durante el día en que se guardó: al otro día el tablero abre en hoy.
 */
const KEY = "route_dispatch_board_view";

function isoDay(date) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function rememberBoardView(view) {
    try {
        sessionStorage.setItem(KEY, JSON.stringify({ ...view, savedOn: isoDay(new Date()) }));
    } catch {
        // navegación privada sin almacenamiento: el tablero abre en hoy
    }
}

export function recallBoardView() {
    try {
        const view = JSON.parse(sessionStorage.getItem(KEY) || "null");
        return view && view.savedOn === isoDay(new Date()) ? view : null;
    } catch {
        return null;
    }
}

/** Día local ("2026-11-23") de una fecha y hora del servidor ("2026-11-23 23:28:00", UTC). */
export function localDay(serverDatetime) {
    const date = serverDatetime ? new Date(serverDatetime.replace(" ", "T") + "Z") : null;
    return date && !isNaN(date) ? isoDay(date) : null;
}

/** Atraso legible: "25 min", "3 h 10", "2 d 8 h". */
export function lateLabel(minutes) {
    const total = Math.max(0, Math.round(minutes || 0));
    if (total < 60) {
        return `${total} min`;
    }
    if (total < 1440) {
        return `${Math.floor(total / 60)} h ${String(total % 60).padStart(2, "0")}`;
    }
    return `${Math.floor(total / 1440)} d ${Math.floor((total % 1440) / 60)} h`;
}
