"""Oturumdaki kullanıcı bilgisi (web portalı rol tabanlı gezinme için kullanır)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from serviszeka.auth import CurrentUserDep, get_current_user
from serviszeka.config import get_settings

router = APIRouter(tags=["me"], dependencies=[Depends(get_current_user)])


class MeRead(BaseModel):
    username: str
    roles: list[str]
    # Arayüzün güven göstergesi bu eşiği çizer (SZ_AI_CONFIDENCE_THRESHOLD); gizli değildir
    ai_confidence_threshold: float


@router.get("/me", response_model=MeRead)
async def me(user: CurrentUserDep) -> MeRead:
    # Her kimliği doğrulanmış kullanıcı kendi bilgisini görebilir (rol gerekmez)
    return MeRead(
        username=user.username,
        roles=sorted(user.roles),
        ai_confidence_threshold=get_settings().ai_confidence_threshold,
    )
