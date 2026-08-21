"""配置中心（§4.6）：system_config / 聚合配置 / 模型价格 / 断言算子 / rubric。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.constants import ALLOWED_CLASS_PATHS
from app.core.db import get_db
from app.core.errors import ApiError, E_VALIDATION
from app.core.response import ok
from app.models import AssertionOpDef, JudgeRubric, MetricDef, ModelPrice, SystemConfig
from app.models.user import User

router = APIRouter(prefix="/config", tags=["config"])

Admin = Depends(require_role("admin"))
Staff = Depends(require_role("admin", "evaluator"))


class ConfigPutBody(BaseModel):
    # {key: value}；已注册的 key 才可改，scope/is_hot 不可由 API 修改
    values: dict


@router.get("/global")
async def get_global_config(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(SystemConfig).order_by(SystemConfig.key))).scalars().all()
    return ok([{"key": r.key, "value": r.value, "scope": r.scope, "is_hot": r.is_hot,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r in rows])


@router.put("/global")
async def put_global_config(body: ConfigPutBody, _: User = Admin, db: AsyncSession = Depends(get_db)):
    for key, value in body.values.items():
        row = await db.get(SystemConfig, key)
        if row is None:
            raise ApiError(E_VALIDATION, f"未知配置项 {key}（必须先在 seed 登记）", 400)
        if not row.is_hot:
            raise ApiError(E_VALIDATION, f"{key} 非热生效（is_hot=false），改需重启", 400)
        row.value = value
    await db.commit()
    return ok()


@router.get("")
async def get_aggregated_config(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """聚合配置（含默认值合并，供前端配置中心完整渲染）。"""
    rows = (await db.execute(select(SystemConfig))).scalars().all()
    return ok({r.key: {"value": r.value, "scope": r.scope, "is_hot": r.is_hot} for r in rows})


# ---------------- 模型价格 ----------------
class PriceCreate(BaseModel):
    model: str = Field(min_length=1, max_length=64)
    input_price: float = Field(ge=0)
    output_price: float = Field(ge=0)
    cache_hit_price: float | None = None
    effective_from: str | None = None  # ISO 时间；缺省当前时间


@router.get("/model-prices")
async def list_prices(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ModelPrice).order_by(ModelPrice.model, ModelPrice.effective_from.desc()))).scalars().all()
    return ok([{"model": r.model, "input_price": float(r.input_price), "output_price": float(r.output_price),
                "cache_hit_price": float(r.cache_hit_price) if r.cache_hit_price is not None else None,
                "effective_from": r.effective_from.isoformat()} for r in rows])


@router.post("/model-prices")
async def create_price(body: PriceCreate, _: User = Admin, db: AsyncSession = Depends(get_db)):
    from datetime import datetime, timezone
    effective = datetime.fromisoformat(body.effective_from) if body.effective_from else datetime.now(timezone.utc)
    db.add(ModelPrice(model=body.model, input_price=body.input_price, output_price=body.output_price,
                      cache_hit_price=body.cache_hit_price, effective_from=effective))
    await db.commit()
    return ok()


# ---------------- 断言算子（admin，白名单校验） ----------------
@router.get("/assertion-ops")
async def list_ops(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AssertionOpDef))).scalars().all()
    return ok([{"op": r.op, "class_path": r.class_path} for r in rows])


class OpCreate(BaseModel):
    op: str = Field(min_length=1, max_length=64)
    class_path: str = Field(min_length=1, max_length=256)


@router.post("/assertion-ops")
async def create_op(body: OpCreate, _: User = Admin, db: AsyncSession = Depends(get_db)):
    if body.class_path not in ALLOWED_CLASS_PATHS:
        raise ApiError(E_VALIDATION, f"class_path 不在白名单：{body.class_path}", 400)
    db.add(AssertionOpDef(op=body.op, class_path=body.class_path))
    await db.commit()
    return ok()


# ---------------- rubric ----------------
class RubricCreate(BaseModel):
    dimension_code: str
    interface_id: int = 0
    version: str = Field(min_length=1, max_length=16)
    template: dict


@router.get("/rubrics")
async def list_rubrics(dimension_code: str | None = None, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(JudgeRubric)
    if dimension_code:
        stmt = stmt.where(JudgeRubric.dimension_code == dimension_code)
    rows = (await db.execute(stmt.order_by(JudgeRubric.id.desc()))).scalars().all()
    return ok([{"id": r.id, "dimension_code": r.dimension_code, "interface_id": r.interface_id,
                "version": r.version, "template": r.template} for r in rows])


@router.post("/rubrics")
async def create_rubric(body: RubricCreate, _: User = Admin, db: AsyncSession = Depends(get_db)):
    db.add(JudgeRubric(dimension_code=body.dimension_code, interface_id=body.interface_id,
                       version=body.version, template=body.template))
    await db.commit()
    return ok()


@router.put("/rubrics/{rubric_id}")
async def update_rubric(rubric_id: int, body: RubricCreate, _: User = Admin, db: AsyncSession = Depends(get_db)):
    rubric = await db.get(JudgeRubric, rubric_id)
    if rubric is None:
        raise ApiError(2001, "rubric 不存在", 404)
    rubric.dimension_code = body.dimension_code
    rubric.interface_id = body.interface_id
    rubric.version = body.version
    rubric.template = body.template
    await db.commit()
    return ok()


@router.delete("/rubrics/{rubric_id}")
async def delete_rubric(rubric_id: int, _: User = Admin, db: AsyncSession = Depends(get_db)):
    rubric = await db.get(JudgeRubric, rubric_id)
    if rubric is None:
        raise ApiError(2001, "rubric 不存在", 404)
    await db.delete(rubric)
    await db.commit()
    return ok()
