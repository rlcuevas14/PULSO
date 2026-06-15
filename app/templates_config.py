from datetime import datetime

from fastapi.templating import Jinja2Templates

from app.items.lifecycle import allowed_targets, non_terminal_targets

templates = Jinja2Templates(directory="app/templates")

# Helpers disponibles en todas las plantillas.
templates.env.globals["non_terminal_targets"] = non_terminal_targets
templates.env.globals["allowed_targets"] = allowed_targets


def _fecha(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Formatea una fecha/hora. Tolera None y valores no-datetime."""
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime(fmt)
    return str(value)


# Filtro Jinja `fecha`: {{ item.created_at | fecha }} → '2026-06-15 14:30'.
templates.env.filters["fecha"] = _fecha
