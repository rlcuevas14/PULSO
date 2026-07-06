"""Webhooks Sentry + GitHub (firmados). Sin auth de sesión: la firma HMAC es la auth."""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.webhooks import service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/sentry")
async def sentry_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    if not settings.sentry_client_secret:
        return JSONResponse({"error": "webhook Sentry no configurado"}, status_code=503)
    body = await request.body()
    sig = request.headers.get("sentry-hook-signature")
    if not service.verify_sentry_signature(settings.sentry_client_secret, body, sig):
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    try:
        payload = json.loads(body)
        result = await service.ingest_sentry(db, payload)
        await db.commit()
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return JSONResponse(result)


@router.post("/github")
async def github_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    if not settings.github_webhook_secret:
        return JSONResponse({"error": "webhook GitHub no configurado"}, status_code=503)
    body = await request.body()
    sig = request.headers.get("x-hub-signature-256")
    if not service.verify_github_signature(settings.github_webhook_secret, body, sig):
        return JSONResponse({"error": "invalid signature"}, status_code=401)

    event = request.headers.get("x-github-event", "")
    if event not in ("push",):
        return JSONResponse({"ignored": event})
    payload = json.loads(body)
    result = await service.process_github_push(db, payload)
    await db.commit()
    return JSONResponse(result)
