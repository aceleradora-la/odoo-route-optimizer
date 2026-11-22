/** @odoo-module **/
import * as owl from "@odoo/owl";

/**
 * El mismo código corre en OWL 2 (Odoo 17 a 19) y OWL 3 (Odoo 20).
 *
 * Lo que cambia entre versiones y cómo se evita en este módulo:
 *  - estado reactivo: `useState` pasó a llamarse `proxy` → `reactive()` de acá abajo;
 *  - plantillas: OWL 3 exige `this.` para llegar al componente; OWL 2 también lo
 *    acepta, así que las plantillas lo usan siempre;
 *  - `t-esc` se fue (se usa `t-out`), `t-model` y `t-ref` cambiaron de sintaxis (se
 *    usan `t-att-value` + `t-on-input` y búsquedas en el DOM);
 *  - `static props` da error en OWL 3: los componentes de acá no declaran props.
 */
export function reactive(value) {
    return owl.proxy ? owl.proxy(value) : owl.useState(value);
}

/**
 * Props de un widget de vista (`<widget name="..."/>`): en OWL 3 hay que declararlas con
 * `useProps`; en OWL 2 ya están en `this.props`. Llamar desde `setup()`.
 */
export function widgetProps(component) {
    if (owl.useProps) {
        return owl.useProps({ record: owl.t.object(), readonly: owl.t.boolean().optional() });
    }
    return component.props;
}

/**
 * Lector de códigos con la cámara. El módulo cambió de lugar entre versiones (hasta
 * Odoo 17 vive en webclient/barcode), así que se busca al usarlo en vez de importarlo:
 * un import a una ruta que no existe rompería toda la app.
 */
export function barcodeApi() {
    const modules = odoo.loader.modules;
    const dialog = modules.get("@web/core/barcode/barcode_dialog");
    const scanner = modules.get("@web/core/barcode/barcode_video_scanner");
    const legacy = modules.get("@web/webclient/barcode/barcode_scanner");
    const BarcodeDialog = (dialog && dialog.BarcodeDialog) || (legacy && legacy.BarcodeDialog);
    const supported = (scanner && scanner.isBarcodeScannerSupported) || (legacy && legacy.isBarcodeScannerSupported);
    return { BarcodeDialog, isSupported: () => Boolean(BarcodeDialog && supported && supported()) };
}
