#!/usr/bin/env python3
"""
Adapta el árbol de trabajo (que sigue la rama 19.0) a Odoo 18.0 o 17.0.

Uso, parado en la rama destino y con el contenido de 19.0 ya copiado:

    git checkout 18.0
    git checkout 19.0 -- .
    python tools/port_to_version.py 18.0

Todas las diferencias conocidas entre versiones están declaradas acá abajo,
así que agregar una funcionalidad nueva solo requiere repetir el copiado y
volver a correr el script. Verificado contra el fuente de cada versión:

  <list> / <tree>          Odoo 19 ya no acepta <tree>; Odoo 17 no acepta <list>
  view_mode                17 usa "tree,form"; 18+ usan "list,form"
  stock_partner_delivery_window   no existe en OCA 17.0 (la integración es
                           opcional: el código ya degrada solo si falta)

El resto del código es tolerante a versión por diseño (ver _extend_domain en
delivery_zone), por eso no aparece acá.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ["route_optimizer", "route_optimizer_fleet", "delivery_zone"]


def bump_manifest_versions(target):
    """Reescribe el prefijo de versión de cada manifest (19.0.1.2.3 -> 18.0.1.2.3)."""
    changed = []
    for module in MODULES:
        manifest = ROOT / module / "__manifest__.py"
        if not manifest.exists():
            continue
        text = manifest.read_text(encoding="utf-8")
        new = re.sub(
            r'("version"\s*:\s*")\d+\.\d+(\.[\d.]+")',
            rf"\g<1>{target}\g<2>",
            text,
        )
        if new != text:
            manifest.write_text(new, encoding="utf-8")
            changed.append(module)
    return changed


def list_to_tree():
    """Odoo 17 usa <tree> en el arch y 'tree,form' en view_mode."""
    changed = []
    for path in list(ROOT.glob("*/views/*.xml")) + list(ROOT.glob("*/models/*.py")):
        text = original = path.read_text(encoding="utf-8")
        if path.suffix == ".xml":
            text = re.sub(r"<list(\s|>)", r"<tree\1", text)
            text = text.replace("</list>", "</tree>")
            text = text.replace(
                "<field name=\"view_mode\">list,form</field>",
                "<field name=\"view_mode\">tree,form</field>",
            )
        else:
            text = text.replace('"view_mode": "list,form"', '"view_mode": "tree,form"')
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed.append(str(path.relative_to(ROOT)))
    return changed


def drop_oca_delivery_window():
    """OCA stock_partner_delivery_window no está portado a 17.0.

    La integración ya es opcional en el código (delivery_windows.py comprueba
    si res.partner tiene delivery_time_preference), así que basta con sacarlo
    de depends para que el módulo instale sin él.
    """
    manifest = ROOT / "route_optimizer" / "__manifest__.py"
    text = manifest.read_text(encoding="utf-8")
    new = re.sub(r'\n\s*"stock_partner_delivery_window",', "", text)
    if new != text:
        manifest.write_text(new, encoding="utf-8")
        return True
    return False


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("17.0", "18.0"):
        print("Uso: python tools/port_to_version.py {17.0|18.0}")
        return 2
    target = sys.argv[1]

    print(f"Adaptando el árbol a Odoo {target}\n")
    changed = bump_manifest_versions(target)
    print(f"  versiones de manifest -> {target}: {', '.join(changed) or 'sin cambios'}")

    if target == "17.0":
        converted = list_to_tree()
        print(f"  <list> -> <tree> / view_mode: {', '.join(converted) or 'sin cambios'}")
        dropped = drop_oca_delivery_window()
        print(
            "  stock_partner_delivery_window quitado de depends"
            if dropped
            else "  stock_partner_delivery_window: no estaba en depends"
        )
    else:
        print("  <list> y view_mode: sin cambios (18.0 usa la misma sintaxis que 19.0)")

    print("\nListo. Revisá con: git diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
