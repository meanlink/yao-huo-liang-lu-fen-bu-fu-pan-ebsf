from enum import Enum


class MaterialKind(str, Enum):
    raw = 'raw'          # 原料
    mixed = 'mixed'      # 混配釉料（多级拆合产物）


class IssueStatus(str, Enum):
    pending = 'pending'      # 缺来源领用，暂列待查
    linked = 'linked'        # 已补登来源批号


class RecallStatus(str, Enum):
    tracing = 'tracing'          # 追查中（可中断恢复）
    ready = 'ready'              # 追查完成，待核对/发布
    rechecking = 'rechecking'    # 关系更新后旧追溯失效，重新核对中
    published = 'published'      # 已整单发布


class CandidateStatus(str, Enum):
    affected = 'affected'      # 受影响
    excluded = 'excluded'      # 已排除（须留排除依据）
    unresolved = 'unresolved'  # 待核对（默认）


class TargetKind(str, Enum):
    kiln = 'kiln'
    review = 'review'
    briefing = 'briefing'
    pending_issue = 'pending_issue'
