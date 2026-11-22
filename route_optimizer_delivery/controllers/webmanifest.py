# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.addons.web.controllers import webmanifest


# Odoo 17 no tiene apps instalables por separado (/scoped_app): ahí no se define nada
# y la app se usa desde el navegador.
if hasattr(webmanifest.WebManifest, "_get_scoped_app_name"):

    class RouteDeliveryWebManifest(webmanifest.WebManifest):
        """App de reparto instalable en el celular con la misma mecánica que Odoo usa
        para Código de Barras: /scoped_app ofrece instalarla y el manifiesto queda
        acotado a /scoped_app/reparto, con nombre e ícono propios."""

        DELIVERY_APP_MODULE = "route_optimizer_delivery"
        DELIVERY_APP_PATH = "reparto"
        DELIVERY_APP_NAME = "Reparto"

        def _route_delivery_is_app(self, app_id):
            return app_id == self.DELIVERY_APP_MODULE and self.DELIVERY_APP_PATH in (
                request.params.get("path") or ""
            )

        def _get_scoped_app_name(self, app_id):
            if self._route_delivery_is_app(app_id):
                return self.DELIVERY_APP_NAME
            return super()._get_scoped_app_name(app_id)

        def _get_scoped_app_icons(self, app_id):
            if self._route_delivery_is_app(app_id):
                return [
                    {
                        "src": "/route_optimizer_delivery/static/description/icon_app_%d.png" % size,
                        "sizes": "%dx%d" % (size, size),
                        "type": "image/png",
                    }
                    for size in (192, 512)
                ]
            return super()._get_scoped_app_icons(app_id)

        @http.route()
        def scoped_app_icon_png(self, app_id, add_padding=False):
            # Ícono para iPhone (apple-touch-icon): la página de instalación no manda el
            # path, y la única app instalable de este módulo es la de reparto.
            if app_id == self.DELIVERY_APP_MODULE:
                return request.redirect("/route_optimizer_delivery/static/description/icon_app_192.png")
            return super().scoped_app_icon_png(app_id, add_padding=add_padding)
