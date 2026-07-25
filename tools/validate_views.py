#!/usr/bin/env python3
"""
Valida las vistas XML de los módulos contra los esquemas RNG oficiales de Odoo.

Atrapa localmente los errores que de otro modo solo aparecen al instalar
(atributos no permitidos, elementos mal ubicados, etc.).

Uso:
    python tools/validate_views.py [version]     # default: 19.0

Los esquemas se descargan una vez a tools/rng/<version>/.
Las vistas heredadas (con xpath / position) se saltean: el RNG no las cubre.
Los <form> tampoco tienen RNG en Odoo moderno (se validan por código).
"""
import sys
import urllib.request
from pathlib import Path

from lxml import etree

RNG_BASE = "https://raw.githubusercontent.com/odoo/odoo/{v}/odoo/addons/base/rng"
# Odoo 17 llama tree_view.rng a lo que 18+ llaman list_view.rng: se intentan ambos.
RNG_FILES = ["common.rng", "search_view.rng", "list_view.rng", "tree_view.rng"]
# tag raíz del arch -> esquema que lo valida
SCHEMA_BY_TAG = {
    "search": "search_view.rng",
    "list": "list_view.rng",
    "tree": "tree_view.rng",
}


def ensure_schemas(version):
    target = Path(__file__).parent / "rng" / version
    target.mkdir(parents=True, exist_ok=True)
    for name in RNG_FILES:
        path = target / name
        if path.exists():
            continue
        url = f"{RNG_BASE.format(v=version)}/{name}"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                path.write_bytes(resp.read())
            print(f"  descargado {version}/{name}")
        except Exception:  # noqa: BLE001
            # Normal: list_view.rng no existe en 17, tree_view.rng no existe en 18+.
            pass
    return target


def is_inherited(arch):
    """Las vistas heredadas usan xpath/position y no siguen el esquema completo."""
    if arch.get("position"):
        return True
    return arch.find(".//xpath") is not None or any(
        child.get("position") for child in arch
    )


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "19.0"
    rng_dir = ensure_schemas(version)

    schemas = {}
    for tag, filename in SCHEMA_BY_TAG.items():
        path = rng_dir / filename
        if path.exists():
            try:
                schemas[tag] = etree.RelaxNG(etree.parse(str(path)))
            except Exception as exc:  # noqa: BLE001
                print(f"  AVISO: esquema {filename} ilegible ({exc})")

    root = Path(__file__).resolve().parent.parent
    errors = checked = skipped = 0

    for xml_file in sorted(root.glob("*/views/*.xml")) + sorted(root.glob("*/report/*.xml")):
        try:
            tree = etree.parse(str(xml_file))
        except etree.XMLSyntaxError as exc:
            print(f"XML MAL FORMADO  {xml_file.relative_to(root)}: {exc}")
            errors += 1
            continue

        for record in tree.iter("record"):
            if record.get("model") != "ir.ui.view":
                continue
            arch = record.find("field[@name='arch']")
            if arch is None or not len(arch):
                continue
            node = arch[0]
            if is_inherited(node) or record.find("field[@name='inherit_id']") is not None:
                skipped += 1
                continue
            schema = schemas.get(node.tag)
            if schema is None:
                skipped += 1
                continue
            checked += 1
            if not schema.validate(node):
                rec_id = record.get("id", "?")
                print(f"INVALIDA  {xml_file.relative_to(root)}  ({rec_id}, <{node.tag}>)")
                for err in schema.error_log:
                    print(f"          {err.message}")
                errors += 1

    print(
        f"\nOdoo {version}: {checked} vista(s) validada(s), "
        f"{skipped} salteada(s) (heredadas/form), {errors} con error."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
