from pydantic import BaseModel, Field


def _qty(name: str):
    return Field(gt=0, alias=name)


class RawBatchIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    supplier: str = Field(default='', max_length=160)
    qty: float = Field(gt=0)


class MixLineIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)   # 批号编码
    qty: float = Field(gt=0)


class MixOrderIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    note: str = Field(default='', max_length=300)
    loss_qty: float = Field(default=0.0, ge=0)
    inputs: list[MixLineIn] = Field(min_length=1)
    outputs: list[MixLineIn] = Field(min_length=1)


class KilnIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    line: str = Field(default='', max_length=80)
    fired: int = Field(ge=0)
    passed: int = Field(ge=0)
    fired_at: float = Field(gt=0)


class LinkedIssueIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    batch_code: str = Field(min_length=1, max_length=80)
    qty: float = Field(gt=0)
    kiln_code: str = Field(min_length=1, max_length=80)


class PendingIssueIn(BaseModel):
    """缺来源领用：只登记物料名称与数量，暂列待查。"""
    code: str = Field(min_length=1, max_length=80)
    material_name: str = Field(min_length=1, max_length=160)
    qty: float = Field(gt=0)
    kiln_code: str = Field(min_length=1, max_length=80)


class IssueResolveIn(BaseModel):
    batch_code: str = Field(min_length=1, max_length=80)


class ReviewIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    kiln_code: str = Field(min_length=1, max_length=80)
    yield_rate: float = Field(ge=0, le=100)
    note: str = Field(default='', max_length=300)


class BriefingIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default='', max_length=4000)
    kiln_codes: list[str] = Field(min_length=1)


class RecallIn(BaseModel):
    batch_code: str = Field(min_length=1, max_length=80)
    reason: str = Field(default='', max_length=300)


class ExclusionIn(BaseModel):
    # 允许空串进入服务层，由服务层返回 400 业务错误并要求填写依据
    reason: str = Field(default='', max_length=1000)


class PublishIn(BaseModel):
    note: str = Field(default='', max_length=1000)
