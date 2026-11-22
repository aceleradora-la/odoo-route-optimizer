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
