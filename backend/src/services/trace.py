"""釉料追溯核心服务：登记、多级混配守恒、防循环、并发防超领、配料图与待查领用。"""
import time

from sqlalchemy import select

from src.models import (
    MaterialBatch, MixOrder, MixLine, Issue, KilnBatch, Review, Briefing,
    BriefingKiln, StockLedger, GraphState,
)


class TraceError(ValueError):
    """业务校验失败（400）。"""


# ---------- 通用 ----------
def now() -> float:
    return time.time()


def get_graph_version(db) -> int:
    row = db.get(GraphState, 1)
    if row is None:
        row = GraphState(id=1, version=1)
        db.add(row)
        db.flush()
    return row.version


def bump_graph_version(db) -> int:
    row = db.get(GraphState, 1)
    if row is None:
        row = GraphState(id=1, version=1)
        db.add(row)
        db.flush()
    row.version += 1
    return row.version


def _get_batch(db, code: str) -> MaterialBatch:
    b = db.scalar(select(MaterialBatch).where(MaterialBatch.code == code))
    if b is None:
        raise TraceError(f'批号不存在：{code}')
    return b


def _require_unique(db, model, code: str, label: str):
    if db.scalar(select(model.id).where(model.code == code)):
        raise TraceError(f'{label}编码已存在：{code}')


def _add_ledger(db, batch_id: int, change: float, balance: float, reason: str, ref_code: str):
    db.add(StockLedger(batch_id=batch_id, change_qty=change, balance_after=balance,
                       reason=reason, ref_code=ref_code, created_at=now()))


# ---------- 原料登记 ----------
def register_raw(db, data) -> MaterialBatch:
    _require_unique(db, MaterialBatch, data.code, '批号')
    b = MaterialBatch(code=data.code, name=data.name, kind='raw', supplier=data.supplier,
                      initial_qty=data.qty, on_hand_qty=data.qty, created_at=now())
    db.add(b)
    db.flush()
    _add_ledger(db, b.id, data.qty, data.qty, 'register', b.code)
    bump_graph_version(db)
    return b


# ---------- 窑次 / 复盘 / 简报 ----------
def register_kiln(db, data) -> KilnBatch:
    _require_unique(db, KilnBatch, data.code, '窑次')
    if data.passed > data.fired:
        raise TraceError('合格件数不能大于烧成件数')
    k = KilnBatch(code=data.code, line=data.line, fired=data.fired, passed=data.passed,
                  fired_at=data.fired_at, created_at=now())
    db.add(k)
    db.flush()
    return k


def register_review(db, data) -> Review:
    _require_unique(db, Review, data.code, '复盘')
    k = db.scalar(select(KilnBatch).where(KilnBatch.code == data.kiln_code))
    if k is None:
        raise TraceError(f'窑次不存在：{data.kiln_code}')
    r = Review(code=data.code, kiln_id=k.id, yield_rate=data.yield_rate,
               note=data.note, created_at=now())
    db.add(r)
    db.flush()
    # 复盘与窑次建立关联，改变召回结果集，旧追溯快照需失效
    bump_graph_version(db)
    return r


def register_briefing(db, data) -> Briefing:
    _require_unique(db, Briefing, data.code, '简报')
    kilns = []
    for code in dict.fromkeys(data.kiln_codes):
        k = db.scalar(select(KilnBatch).where(KilnBatch.code == code))
        if k is None:
            raise TraceError(f'窑次不存在：{code}')
        kilns.append(k)
    b = Briefing(code=data.code, title=data.title, content=data.content, created_at=now())
    db.add(b)
    db.flush()
    for k in kilns:
        db.add(BriefingKiln(briefing_id=b.id, kiln_id=k.id))
    db.flush()
    # 简报与窑次建立关联，改变召回结果集，旧追溯快照需失效
    bump_graph_version(db)
    return b


# ---------- 配料图 ----------
def build_graph(db):
    """构造正向配料图：原料 -> 混配产物 -> ... -> 领用 -> 窑次。"""
    batches = {b.id: b for b in db.scalars(select(MaterialBatch))}
    edges = {}                       # batch_id -> [(downstream_batch_id, mix_code, qty)]
    mix_inputs = {}                  # mix_id -> [batch_id]
    for line in db.scalars(select(MixLine)):
        if line.direction == 'in':
            mix_inputs.setdefault(line.mix_order_id, []).append(line.batch_id)
    orders = {m.id: m for m in db.scalars(select(MixOrder))}
    for line in db.scalars(select(MixLine)):
        if line.direction == 'in':
            continue
        order = orders.get(line.mix_order_id)
        for in_bid in mix_inputs.get(line.mix_order_id, []):
            edges.setdefault(in_bid, []).append((line.batch_id, order.code if order else str(line.mix_order_id), line.qty))
    issues = db.scalars(select(Issue).where(Issue.batch_id.is_not(None))).all()
    issue_by_batch = {}
    for iss in issues:
        issue_by_batch.setdefault(iss.batch_id, []).append(iss)
    pending = db.scalars(select(Issue).where(Issue.batch_id.is_(None))).all()
    return {'batches': batches, 'edges': edges, 'issues': issue_by_batch, 'pending': pending}


def detect_cycle(db, input_ids: list[int], output_ids: list[int]):
    """若任一 output 已经是某个 input 的祖先（即 output -> ... -> input），则本次混配成环，拒绝。"""
    graph = build_graph(db)
    upstream = set()
    for out_id in output_ids:
        stack = [out_id]
        seen = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for down, _, _ in graph['edges'].get(cur, []):
                stack.append(down)
        if seen & set(input_ids):
            b = db.get(MaterialBatch, out_id)
            raise TraceError(f'该混配会形成配料循环，已拒绝（涉及批号 {b.code if b else out_id}）')


# ---------- 混配（多级拆合，投料与余量守恒） ----------
def create_mix(db, data) -> MixOrder:
    if len({l.code for l in data.inputs}) != len(data.inputs):
        raise TraceError('投入批号重复')
    if len({l.code for l in data.outputs}) != len(data.outputs):
        raise TraceError('产出批号重复')
    if set(l.code for l in data.inputs) & set(l.code for l in data.outputs):
        raise TraceError('同一批号不能同时作为投入与产出')

    inputs, outputs = [], []
    for line in data.inputs:
        b = _get_batch(db, line.code)
        inputs.append((b, line.qty))
    out_records = []
    for line in data.outputs:
        existing = db.scalar(select(MaterialBatch).where(MaterialBatch.code == line.code))
        out_records.append((existing, line.code, line.qty))
        if existing is not None and existing.on_hand_qty > 0:
            raise TraceError(f'产出批号已存在且有余量，不能重复产出：{line.code}')

    detect_cycle(db, [b.id for b, _ in inputs],
                 [b.id for b, _, _ in out_records if b is not None])

    total_in = sum(q for _, q in inputs)
    total_out = sum(q for _, _, q in out_records)
    if round(total_in - total_out - data.loss_qty, 9) != 0:
        raise TraceError(
            f'守恒校验失败：投入 {total_in} ≠ 产出 {total_out} + 损耗 {data.loss_qty}')

    # 行锁校验余量，拒绝超投
    for b, qty in inputs:
        db.refresh(b, with_for_update=True)
        if b.on_hand_qty + 1e-9 < qty:
            raise TraceError(f'投料超过余量：批号 {b.code} 余量 {b.on_hand_qty}，申请 {qty}')

    _require_unique(db, MixOrder, data.code, '混配单')
    order = MixOrder(code=data.code, note=data.note, loss_qty=data.loss_qty,
                     graph_version=get_graph_version(db), created_at=now())
    db.add(order)
    db.flush()

    for b, qty in inputs:
        b.on_hand_qty -= qty
        _add_ledger(db, b.id, -qty, b.on_hand_qty, 'mix_in', order.code)
        db.add(MixLine(mix_order_id=order.id, direction='in', batch_id=b.id, qty=qty))

    for existing, code, qty in out_records:
        if existing is None:
            b = MaterialBatch(code=code, name=code, kind='mixed',
                              initial_qty=0.0, on_hand_qty=0.0, created_at=now())
            db.add(b)
            db.flush()
        else:
            b = existing
            b.kind = 'mixed'
        b.initial_qty += qty
        b.on_hand_qty += qty
        _add_ledger(db, b.id, qty, b.on_hand_qty, 'mix_out', order.code)
        db.add(MixLine(mix_order_id=order.id, direction='out', batch_id=b.id, qty=qty))

    bump_graph_version(db)
    return order


# ---------- 领用 ----------
def _get_kiln(db, code: str) -> KilnBatch:
    k = db.scalar(select(KilnBatch).where(KilnBatch.code == code))
    if k is None:
        raise TraceError(f'窑次不存在：{code}')
    return k


def create_issue(db, data) -> Issue:
    """有来源领用：行锁 + 条件更新，并发超领拒绝。"""
    _require_unique(db, Issue, data.code, '领用单')
    kiln = _get_kiln(db, data.kiln_code)
    batch = _get_batch(db, data.batch_code)
    db.refresh(batch, with_for_update=True)
    if batch.on_hand_qty + 1e-9 < data.qty:
        raise TraceError(f'领用超过余量：批号 {batch.code} 余量 {batch.on_hand_qty}，申请 {data.qty}')
    batch.on_hand_qty -= data.qty
    _add_ledger(db, batch.id, -data.qty, batch.on_hand_qty, 'issue', data.code)
    iss = Issue(code=data.code, batch_id=batch.id, material_name=batch.name,
                qty=data.qty, status='linked', kiln_id=kiln.id, created_at=now())
    db.add(iss)
    db.flush()
    bump_graph_version(db)
    return iss


def create_pending_issue(db, data) -> Issue:
    """缺来源领用：暂列待查，绝不当作无影响。"""
    _require_unique(db, Issue, data.code, '领用单')
    kiln = _get_kiln(db, data.kiln_code)
    iss = Issue(code=data.code, batch_id=None, material_name=data.material_name,
                qty=data.qty, status='pending', kiln_id=kiln.id, created_at=now())
    db.add(iss)
    db.flush()
    bump_graph_version(db)
    return iss


def resolve_pending_issue(db, issue_id: int, data) -> Issue:
    iss = db.get(Issue, issue_id)
    if iss is None or iss.status != 'pending':
        raise TraceError('待查领用不存在或已补登')
    # 召回封锁期间不得补登（避免绕过追溯）
    from src.services.recall import issue_is_blocked
    if issue_is_blocked(db, iss.id):
        raise TraceError('该待查领用处于召回暂停范围，发布后再补登来源')
    batch = _get_batch(db, data.batch_code)
    db.refresh(batch, with_for_update=True)
    if batch.on_hand_qty + 1e-9 < iss.qty:
        raise TraceError(f'补登失败，批号 {batch.code} 余量 {batch.on_hand_qty} 不足 {iss.qty}')
    batch.on_hand_qty -= iss.qty
    _add_ledger(db, batch.id, -iss.qty, batch.on_hand_qty, 'issue', iss.code)
    iss.batch_id = batch.id
    iss.material_name = batch.name
    iss.status = 'linked'
    bump_graph_version(db)
    return iss


# ---------- 基因链（页面展示用） ----------
def genealogy(db, code: str) -> dict:
    batch = db.scalar(select(MaterialBatch).where(MaterialBatch.code == code))
    if batch is None:
        raise TraceError(f'批号不存在：{code}')
    lines = db.scalars(select(MixLine).where(MixLine.batch_id == batch.id)).all()
    order_ids = {l.mix_order_id for l in lines}
    orders = db.scalars(select(MixOrder).where(MixOrder.id.in_(order_ids))).all() if order_ids else []
    result = {'batch': batch_dict(batch), 'produced_by': [], 'consumed_in': [], 'issues': []}
    for o in orders:
        all_lines = db.scalars(select(MixLine).where(MixLine.mix_order_id == o.id)).all()
        node = {'mix_code': o.code, 'loss_qty': o.loss_qty, 'inputs': [], 'outputs': []}
        for l in all_lines:
            b = db.get(MaterialBatch, l.batch_id)
            item = {'code': b.code, 'name': b.name, 'qty': l.qty}
            (node['inputs'] if l.direction == 'in' else node['outputs']).append(item)
        if any(l.direction == 'out' and l.batch_id == batch.id for l in all_lines):
            result['produced_by'].append(node)
        else:
            result['consumed_in'].append(node)
    for iss in db.scalars(select(Issue).where(Issue.batch_id == batch.id)):
        k = db.get(KilnBatch, iss.kiln_id)
        result['issues'].append({'code': iss.code, 'qty': iss.qty,
                                 'status': iss.status, 'kiln_code': k.code if k else ''})
    return result


# ---------- 序列化 ----------
def batch_dict(b: MaterialBatch) -> dict:
    return {'id': b.id, 'code': b.code, 'name': b.name, 'kind': b.kind,
            'supplier': b.supplier, 'initial_qty': b.initial_qty,
            'on_hand_qty': b.on_hand_qty, 'created_at': b.created_at}


def issue_dict(i: Issue, kiln_code: str = '') -> dict:
    return {'id': i.id, 'code': i.code, 'batch_id': i.batch_id,
            'material_name': i.material_name, 'qty': i.qty, 'status': i.status,
            'kiln_id': i.kiln_id, 'kiln_code': kiln_code, 'created_at': i.created_at}


def kiln_dict(k: KilnBatch) -> dict:
    return {'id': k.id, 'code': k.code, 'line': k.line, 'fired': k.fired,
            'passed': k.passed, 'fired_at': k.fired_at, 'created_at': k.created_at}
