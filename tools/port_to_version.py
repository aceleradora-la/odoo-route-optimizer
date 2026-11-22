#!/usr/bin/env python3
"""
Adapta el árbol de trabajo (que sigue la rama 19.0) a Odoo 20.0, 18.0 o 17.0.

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

  ir.model.access.csv      Odoo 20 usa security/ir.access.csv (otro formato)
  stock_picking_batch      en Odoo 20 se fusionó dentro de `stock` (mismos ids)

El resto del código es tolerante a versión por diseño (ver _extend_domain en
delivery_zone), por eso no aparece acá.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ["route_optimizer", "route_optimizer_fleet", "delivery_zone", "route_optimizer_delivery"]


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


def batch_module_into_stock():
    """En Odoo 20 los traslados por lote son parte de `stock`.

    El módulo `stock_picking_batch` dejó de existir: sus modelos, vistas e ids
    externos conservan el nombre pero pasaron al módulo `stock`.
    """
    changed = []
    for path in sorted(ROOT.glob("*/__manifest__.py")) + sorted(ROOT.glob("*/*/*.xml")):
        text = original = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            text = re.sub(r'"stock_picking_batch",', '"stock",', text)
        else:
            # La herencia del formulario del tipo de operación se fundió en el original.
            text = text.replace(
                'ref="stock_picking_batch.view_picking_type_form_inherit"',
                'ref="stock.view_picking_type_form"',
            )
            text = text.replace('ref="stock_picking_batch.', 'ref="stock.')
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed.append(str(path.relative_to(ROOT)))
    return changed


def access_csv_to_ir_access():
    """Odoo 20 reemplazó `ir.model.access` (e `ir.rule`) por `ir.access`.

    El archivo pasa a llamarse security/ir.access.csv, el modelo se nombra por su
    nombre técnico (stock.picking) y los cuatro permisos se escriben juntos como
    una combinación de letras: c(reate) r(ead) u(pdate) d(elete).
    """
    import csv
    import io

    changed = []
    # model_route_optimizer_wizard -> route.optimizer.wizard: el id externo no dice
    # dónde van los puntos, así que se busca entre los _name declarados.
    names = {}
    for source in ROOT.glob("*/*/*.py"):
        for name in re.findall(r'^\s+_name = "([\w.]+)"', source.read_text(encoding="utf-8"), flags=re.M):
            names["model_" + name.replace(".", "_")] = name
    for path in sorted(ROOT.glob("*/security/ir.model.access.csv")):
        rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["id", "name", "model_id", "group_id/id", "operation", "domain"])
        for row in rows:
            model = row["model_id:id"].split(".")[-1]
            if model not in names:
                raise SystemExit(f"{path}: no encuentro el modelo de {model}")
            flags = [("c", "perm_create"), ("r", "perm_read"), ("u", "perm_write"), ("d", "perm_unlink")]
            operation = "".join(letter for letter, column in flags if row[column] == "1")
            writer.writerow([row["id"], row["name"], names[model], row["group_id:id"], operation, ""])
        (path.parent / "ir.access.csv").write_text(out.getvalue(), encoding="utf-8")
        path.unlink()
        manifest = path.parent.parent / "__manifest__.py"
        text = manifest.read_text(encoding="utf-8")
        manifest.write_text(text.replace("security/ir.model.access.csv", "security/ir.access.csv"), encoding="utf-8")
        changed.append(str(path.parent.parent.name))
    return changed


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("17.0", "18.0", "20.0"):
        print("Uso: python tools/port_to_version.py {17.0|18.0|20.0}")
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
    elif target == "20.0":
        converted = access_csv_to_ir_access()
        print(f"  ir.model.access.csv -> ir.access.csv: {', '.join(converted) or 'sin cambios'}")
        moved = batch_module_into_stock()
        print(f"  stock_picking_batch -> stock: {', '.join(moved) or 'sin cambios'}")
        # OCA todavía no publicó stock_partner_delivery_window para 20.0. La integración
        # es opcional en el código: se saca de depends hasta que exista.
        dropped = drop_oca_delivery_window()
        print(
            "  stock_partner_delivery_window quitado de depends (sin port de OCA a 20.0)"
            if dropped
            else "  stock_partner_delivery_window: no estaba en depends"
        )
    else:
        print("  <list> y view_mode: sin cambios (18.0 usa la misma sintaxis que 19.0)")

    print("\nListo. Revisá con: git diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
