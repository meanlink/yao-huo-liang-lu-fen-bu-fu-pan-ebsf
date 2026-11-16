from sqlalchemy import String, Double, Integer, ForeignKey, Boolean, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.config.database import Base


class MaterialBatch(Base):
    """原料批号与混配釉料批号的统一台账。"""
    __tablename__ = 'trace_material_batches'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(16))          # raw / mixed
    supplier: Mapped[str] = mapped_column(String(160), default='')
    initial_qty: Mapped[float] = mapped_column(Double)     # 登记/产出总量
    on_hand_qty: Mapped[float] = mapped_column(Double)     # 守恒后的余量
    created_at: Mapped[float] = mapped_column(Double)


class MixOrder(Base):
    """一次混配：多级拆合中的一个节点。inputs 守恒于 outputs（允许非负损耗）。"""
    __tablename__ = 'trace_mix_orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    note: Mapped[str] = mapped_column(String(300), default='')
    loss_qty: Mapped[float] = mapped_column(Double, default=0.0)
    graph_version: Mapped[int] = mapped_column(Integer, default=1)  # 创建时的关系版本
    created_at: Mapped[float] = mapped_column(Double)


class MixLine(Base):
    __tablename__ = 'trace_mix_lines'
    id: Mapped[int] = mapped_column(primary_key=True)
    mix_order_id: Mapped[int] = mapped_column(ForeignKey('trace_mix_orders.id', ondelete='CASCADE'), index=True)
    direction: Mapped[str] = mapped_column(String(8))       # in / out
    batch_id: Mapped[int] = mapped_column(ForeignKey('trace_material_batches.id'), index=True)
    qty: Mapped[float] = mapped_column(Double)
    __table_args__ = (Index('ix_mix_line_dir_batch', 'direction', 'batch_id'),)


class Issue(Base):
    """领用：混配/釉料被窑次耗用。缺来源批号的领用暂列待查，不得当作无影响。"""
    __tablename__ = 'trace_issues'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey('trace_material_batches.id'), nullable=True, index=True)
    # 缺来源时仅登记物料名称，batch_id 为空且 status=pending
    material_name: Mapped[str] = mapped_column(String(160), default='')
    qty: Mapped[float] = mapped_column(Double)
    status: Mapped[str] = mapped_column(String(16), default='linked')  # linked / pending
    kiln_id: Mapped[int] = mapped_column(ForeignKey('trace_kiln_batches.id'), index=True)
    created_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (Index('ix_issue_status', 'status'),)


class KilnBatch(Base):
    __tablename__ = 'trace_kiln_batches'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    line: Mapped[str] = mapped_column(String(80), default='')
    fired: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    fired_at: Mapped[float] = mapped_column(Double)
    created_at: Mapped[float] = mapped_column(Double)


class Review(Base):
    """窑次良率复盘。"""
    __tablename__ = 'trace_reviews'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    kiln_id: Mapped[int] = mapped_column(ForeignKey('trace_kiln_batches.id'), index=True)
    yield_rate: Mapped[float] = mapped_column(Double)
    note: Mapped[str] = mapped_column(String(300), default='')
    created_at: Mapped[float] = mapped_column(Double)


class Briefing(Base):
    """班组复核简报，可覆盖多个窑次。"""
    __tablename__ = 'trace_briefings'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[float] = mapped_column(Double)


class BriefingKiln(Base):
    __tablename__ = 'trace_briefing_kilns'
    id: Mapped[int] = mapped_column(primary_key=True)
    briefing_id: Mapped[int] = mapped_column(ForeignKey('trace_briefings.id', ondelete='CASCADE'), index=True)
    kiln_id: Mapped[int] = mapped_column(ForeignKey('trace_kiln_batches.id'), index=True)
    __table_args__ = (UniqueConstraint('briefing_id', 'kiln_id', name='uq_briefing_kiln'),)


class StockLedger(Base):
    """守恒流水：每一次余量变动留痕，便于审计与重放。"""
    __tablename__ = 'trace_stock_ledger'
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('trace_material_batches.id'), index=True)
    change_qty: Mapped[float] = mapped_column(Double)   # 入库为正、耗用为负
    balance_after: Mapped[float] = mapped_column(Double)
    reason: Mapped[str] = mapped_column(String(80))      # register / mix_in / mix_out / issue
    ref_code: Mapped[str] = mapped_column(String(80), default='')
    created_at: Mapped[float] = mapped_column(Double)


class GraphState(Base):
    """单行表：配料关系版本号。任何原料/混配/领用关系写入都使其 +1。"""
    __tablename__ = 'trace_graph_state'
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Recall(Base):
    __tablename__ = 'trace_recalls'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    root_batch_id: Mapped[int] = mapped_column(ForeignKey('trace_material_batches.id'), index=True)
    reason: Mapped[str] = mapped_column(String(300), default='')
    status: Mapped[str] = mapped_column(String(16), default='tracing')
    graph_version: Mapped[int] = mapped_column(Integer)            # 追查快照版本
    frontier: Mapped[str] = mapped_column(Text, default='')        # 断点：待展开批号 JSON
    visited: Mapped[str] = mapped_column(Text, default='[]')       # 已展开批号 JSON
    total_discovered: Mapped[int] = mapped_column(Integer, default=0)
    total_processed: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)


class RecallCandidate(Base):
    __tablename__ = 'trace_recall_candidates'
    id: Mapped[int] = mapped_column(primary_key=True)
    recall_id: Mapped[int] = mapped_column(ForeignKey('trace_recalls.id', ondelete='CASCADE'), index=True)
    target_kind: Mapped[str] = mapped_column(String(16))   # kiln / review / briefing / pending_issue
    target_id: Mapped[int] = mapped_column(Integer)
    target_code: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default='unresolved')
    paths_json: Mapped[str] = mapped_column(Text, default='[]')   # 沿配料链的追查路径
    exclusion_reason: Mapped[str] = mapped_column(Text, default='')
    blocked: Mapped[bool] = mapped_column(Boolean, default=True)  # 发布前一律暂停
    __table_args__ = (UniqueConstraint('recall_id', 'target_kind', 'target_id', name='uq_recall_target'),)


class RecallBlock(Base):
    """封锁登记：保证重试幂等，不重复封锁；发布后按受影响/排除分别处置。"""
    __tablename__ = 'trace_recall_blocks'
    id: Mapped[int] = mapped_column(primary_key=True)
    recall_id: Mapped[int] = mapped_column(ForeignKey('trace_recalls.id', ondelete='CASCADE'), index=True)
    target_kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(String(300), default='')
    created_at: Mapped[float] = mapped_column(Double)
    released_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    __table_args__ = (UniqueConstraint('recall_id', 'target_kind', 'target_id', name='uq_block_target'),)
