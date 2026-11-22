# -*- coding: utf-8 -*-
"""Parámetros del sistema en cualquier versión de Odoo.

Odoo 20 reemplazó ``get_param`` / ``set_param`` por métodos con tipo
(``get_str``, ``get_bool``, ``set_str``...). El código de estos módulos guarda y
lee todo como texto y lo interpreta por su cuenta, así que en Odoo 20 se vuelven
a exponer los dos nombres de siempre sobre los métodos nuevos. En Odoo 17 a 19
este archivo no cambia nada.
"""
from odoo import api, models

import odoo.addons.base.models.ir_config_parameter as base_parameters

_core = getattr(base_parameters, "IrConfig_Parameter", None) or getattr(
    base_parameters, "IrConfigParameter", None
)
_HAS_LEGACY_API = _core is None or hasattr(_core, "get_param")


class IrConfigParameter(models.Model):
    _inherit = "ir.config_parameter"

    if not _HAS_LEGACY_API:

        @api.model
        def get_param(self, key, default=False):
            value = self.get_str(key, None)
            return default if value is None else value

        @api.model
        def set_param(self, key, value):
            return self.set_str(key, value)
