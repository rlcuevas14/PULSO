from fastapi.templating import Jinja2Templates

from app.items.lifecycle import allowed_targets, non_terminal_targets

templates = Jinja2Templates(directory="app/templates")

# Helpers disponibles en todas las plantillas.
templates.env.globals["non_terminal_targets"] = non_terminal_targets
templates.env.globals["allowed_targets"] = allowed_targets
