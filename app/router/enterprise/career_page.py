"""Career-page settings: how the company's public jobs page presents itself.

Croar already serves the page at /jobs?company=<slug>; what it lacked was anything to say on
it beyond the job list. These are the settings behind it — branding, contact details, social
links, the policies a candidate agrees to, and the embed snippets for putting the listing on
the company's own site.

Stored in ``Company.config["career_page"]`` rather than new columns. It is a JSONB blob that
nothing else writes to, the shape is presentational and will keep changing, and a migration per
field would be the wrong trade for that.

Two endpoints read this: an authenticated one for the settings screen, and a public one the
jobs page itself calls. The public one is a deliberate subset — the settings blob is not
sensitive today, but a page served to anonymous visitors should return the fields it needs by
name, so a field added later for internal use is not published by accident.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.company import Company
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/career-page", tags=["Enterprise Career Page"])

CONFIG_KEY = "career_page"


class CareerPageSettings(BaseModel):
    """Everything the public jobs page renders around the job list."""

    # Presentation
    headline: str = Field("", max_length=120)
    intro: str = Field("", max_length=600)
    brand_color: str = Field("", max_length=32)
    logo_url: str = Field("", max_length=500)
    cover_url: str = Field("", max_length=500)

    # Contact
    contact_email: str = Field("", max_length=200)
    contact_phone: str = Field("", max_length=60)
    website: str = Field("", max_length=300)

    # Social
    linkedin: str = Field("", max_length=300)
    twitter: str = Field("", max_length=300)
    facebook: str = Field("", max_length=300)
    instagram: str = Field("", max_length=300)
    youtube: str = Field("", max_length=300)
    show_share_buttons: bool = True

    # Policy — shown to a candidate before they submit
    application_terms: str = Field("", max_length=20000)
    privacy_policy: str = Field("", max_length=20000)

    # Analytics
    ga_measurement_id: str = Field("", max_length=40)


async def _company(session: Any, current_user: object) -> Company:
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")
    company = (await session.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _settings_of(company: Company) -> dict[str, Any]:
    stored = (company.config or {}).get(CONFIG_KEY) or {}
    # Validated on the way out as well as in: a blob edited by hand, or written by an older
    # version of this model, should not break the page that renders it.
    return CareerPageSettings(
        **{k: v for k, v in stored.items() if k in CareerPageSettings.model_fields}
    ).model_dump()


@router.get("/settings")
async def get_career_page_settings(
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> dict[str, Any]:
    company = await _company(session, current_user)
    return {
        "slug": company.slug,
        "company_name": company.name,
        "company_logo": company.logo_url,
        "settings": _settings_of(company),
    }


@router.put("/settings")
async def save_career_page_settings(
    body: CareerPageSettings,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    company = await _company(session, current_user)
    config = dict(company.config or {})
    config[CONFIG_KEY] = body.model_dump()
    company.config = config
    # JSONB reassignment is not always seen as a change by the ORM — without this the save
    # silently does nothing.
    flag_modified(company, "config")
    await session.commit()
    return {"status": "saved", "settings": body.model_dump()}
