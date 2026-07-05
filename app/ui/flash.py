from fastapi import Request


def flash_success(request: Request, *, message: str = "", title: str = "", celebrate: bool = False) -> None:
    """Señal de éxito de un solo uso; base.html la extrae (pop) y la pinta en el siguiente render.

    celebrate=True → overlay central «¡Completado!» (solo completar ítem/hilo).
    celebrate=False → toast verde inferior-derecha.
    """
    request.session["flash_success"] = {"message": message, "title": title, "celebrate": celebrate}
