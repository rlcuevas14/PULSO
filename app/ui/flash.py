from fastapi import Request


def flash_success(request: Request, *, message: str = "", title: str = "", celebrate: bool = False) -> None:
    """One-shot success signal; base.html pops it and renders it on the next render.

    celebrate=True → centered "Completed!" overlay (only for completing an item/thread).
    celebrate=False → green bottom-right toast.
    """
    request.session["flash_success"] = {"message": message, "title": title, "celebrate": celebrate}
