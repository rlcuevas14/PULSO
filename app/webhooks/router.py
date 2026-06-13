"""Router de webhooks (Sentry, GitHub) — implementado en Sprint 5."""

from fastapi import APIRouter

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
