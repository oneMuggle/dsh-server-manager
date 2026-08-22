from __future__ import annotations

from . import render_template


UPLOAD_WIDGET = render_template("upload-widget.html").encode("utf-8")
ADMIN_WIDGET = render_template("admin-widget.html").encode("utf-8")
LOGOUT_WIDGET = render_template("logout-widget.html").encode("utf-8")
