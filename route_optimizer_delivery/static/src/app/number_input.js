/**
 * Números tipeados en el celular (app de reparto).
 *
 * Los campos son <input type="text" inputmode="numeric|decimal">, no type="number": un campo
 * numérico del navegador devuelve "" mientras se tipea un decimal a medias ("74.") y, si está
 * enlazado al estado, el redibujo borra lo tipeado. Además así se acepta la coma decimal del
 * teclado en español.
 */
/** Texto tipeado → número (0 si está vacío o no es un número). Acepta coma o punto decimal. */
export function parseNum(text) {
    if (text === null || text === undefined) {
        return 0;
    }
    const clean = String(text).trim().replace(/\s/g, "");
    if (!clean) {
        return 0;
    }
    const lastComma = clean.lastIndexOf(",");
    const lastDot = clean.lastIndexOf(".");
    let normalized = clean;
    if (lastComma >= 0 && lastDot >= 0) {
        // Con los dos separadores, el último es el decimal (1.234,5 o 1,234.5).
        normalized = lastComma > lastDot ? clean.replace(/\./g, "").replace(",", ".") : clean.replace(/,/g, "");
    } else if (lastComma >= 0) {
        normalized = clean.replace(",", ".");
    }
    const value = parseFloat(normalized);
    return Number.isFinite(value) && value > 0 ? value : 0;
}

/** Número → texto para la celda: sin separador de miles y con coma decimal. "" si es 0. */
export function formatNum(value) {
    if (!value) {
        return "";
    }
    const rounded = Math.round(value * 100) / 100;
    return String(rounded).replace(".", ",");
}

/** Normaliza lo tipeado ("74.40" → "74,4", "abc" → ""). */
export function normalizeNum(text) {
    return formatNum(parseNum(text));
}

/**
 * Al tocar una celda con valor, el celular deja el cursor donde se tocó y lo tipeado se
 * intercala con el número anterior: se selecciona todo para reemplazarlo.
 */
export function selectOnFocus(ev) {
    const input = ev.target;
    // En cuanto se tipea algo no se vuelve a seleccionar: se perdería lo ya tipeado.
    let typed = false;
    const onInput = () => {
        typed = true;
    };
    const select = () => {
        if (!typed && document.activeElement === input && input.select) {
            input.select();
        }
    };
    input.addEventListener("input", onInput, { once: true });
    setTimeout(select, 0);
    // El toque que dio el foco termina después (mouseup/click) y ahí el navegador deja el
    // cursor donde se tocó: se vuelve a seleccionar justo después de ese toque. Solo ese
    // primer toque; un segundo toque sí ubica el cursor para corregir un dígito.
    const afterTap = () => setTimeout(select, 0);
    input.addEventListener("click", afterTap, { once: true });
    setTimeout(() => {
        input.removeEventListener("click", afterTap);
        input.removeEventListener("input", onInput);
    }, 600);
}
