import { Component, onMounted, useRef, useState, xml } from "@odoo/owl";

/**
 * Firma con el dedo. Avisa por `onChange` con la imagen (PNG en data URL) o null al borrar.
 */
export class RouteDeliverySignaturePad extends Component {
    static template = xml`
        <div class="o_rd_signature">
            <canvas t-ref="canvas" class="o_rd_signature_canvas"
                    t-on-pointerdown="onDown" t-on-pointermove="onMove"
                    t-on-pointerup="onUp" t-on-pointercancel="onUp" t-on-pointerleave="onUp"/>
            <div t-if="!state.dirty" class="o_rd_signature_hint">Firmá acá con el dedo</div>
            <button t-if="state.dirty" type="button" class="o_rd_signature_clear" t-on-click="clear">Borrar</button>
        </div>`;
    static props = { onChange: Function };

    setup() {
        this.canvasRef = useRef("canvas");
        this.state = useState({ dirty: false });
        this.drawing = false;
        onMounted(() => this.resize());
    }

    get canvas() {
        return this.canvasRef.el;
    }

    /** El lienzo se dimensiona en píxeles reales para que el trazo no salga pixelado. */
    resize() {
        const canvas = this.canvas;
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const rect = canvas.getBoundingClientRect();
        canvas.width = Math.max(1, Math.round(rect.width * ratio));
        canvas.height = Math.max(1, Math.round(rect.height * ratio));
        const ctx = canvas.getContext("2d");
        ctx.scale(ratio, ratio);
        ctx.lineWidth = 2.2;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.strokeStyle = "#111827";
    }

    point(ev) {
        const rect = this.canvas.getBoundingClientRect();
        return [ev.clientX - rect.left, ev.clientY - rect.top];
    }

    onDown(ev) {
        ev.preventDefault();
        this.canvas.setPointerCapture?.(ev.pointerId);
        this.drawing = true;
        const [x, y] = this.point(ev);
        const ctx = this.canvas.getContext("2d");
        ctx.beginPath();
        ctx.moveTo(x, y);
        // Un toque sin arrastre también deja marca.
        ctx.lineTo(x + 0.1, y + 0.1);
        ctx.stroke();
    }

    onMove(ev) {
        if (!this.drawing) {
            return;
        }
        ev.preventDefault();
        const [x, y] = this.point(ev);
        const ctx = this.canvas.getContext("2d");
        ctx.lineTo(x, y);
        ctx.stroke();
    }

    onUp() {
        if (!this.drawing) {
            return;
        }
        this.drawing = false;
        this.state.dirty = true;
        this.props.onChange(this.canvas.toDataURL("image/png"));
    }

    clear() {
        const canvas = this.canvas;
        canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
        this.state.dirty = false;
        this.props.onChange(null);
    }
}
