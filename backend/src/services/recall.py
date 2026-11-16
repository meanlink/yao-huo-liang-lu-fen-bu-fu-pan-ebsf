"""按原料批号发起的召回：沿配料链 BFS 追查、断点恢复、幂等封锁、版本失效与整单发布。"""
import json
import time
from collections import deque

from sqlalchemy import select

from src.config.database import Session
from src.constants.trace_enums import RecallStatus, CandidateStatus, TargetKind
from src.models import (
    MaterialBatch, MixOrder, MixLine, Issue, KilnBatch, Review, Briefing,
    BriefingKiln, Recall, RecallCandidate, RecallBlock,
)
from src.services import trace as trace_svc


class RecallError(ValueError):
    pass


def now():
    return time.time()


# ---------- 封锁查询（审批/下载门禁用） ----------
def _active_block(db, target_kind: str, target_id: int, recall_id: int | None = None):
    q = select(RecallBlock).where(
        RecallBlock.target_kind == target_kind,
        RecallBlock.target_id == target_id,
        RecallBlock.active.is_(True),
    )
    if recall_id is not None:
        q = q.where(RecallBlock.recall_id == recall_id)
    return db.scalar(q)


def kiln_blocked(db, kiln_id: int) -> RecallBlock | None:
    return _active_block(db, TargetKind.kiln.value, kiln_id)


def review_blocked(db, review_id: int) -> RecallBlock | None:
    return _active_block(db, TargetKind.review.value, review_id)


def briefing_blocked(db, briefing_id: int) -> RecallBlock | None:
    return _active_block(db, TargetKind.briefing.value, briefing_id)


def issue_is_blocked(db, issue_id: int) -> bool:
    return _active_block(db, TargetKind.pending_issue.value, issue_id) is not None


def ensure_unblocked(db, target_kind: str, target_id: int, action: str):
    blk = _active_block(db, target_kind, target_id)
    if blk is not None:
        r = db.get(Recall, blk.recall_id)
        raise RecallError(
            f'{action}已暂停：召回单 {r.code if r else blk.recall_id} 发布前相关结果不可批准或下载')


# ---------- 召回创建 ----------
def create_recall(db, data) -> Recall:
    batch = db.scalar(select(MaterialBatch).where(MaterialBatch.code == data.batch_code))
    if batch is None:
        raise RecallError(f'批号不存在：{data.batch_code}')
    existing = db.scalar(select(Recall).where(
        Recall.root_batch_id == batch.id,
        Recall.status.in_([RecallStatus.tracing.value, RecallStatus.ready.value,
                           RecallStatus.rechecking.value]),
    ))
    if existing is not None:
        raise RecallError(f'该批号已有进行中的召回单：{existing.code}')
    version = trace_svc.get_graph_version(db)
    r = Recall(code=f'RC-{batch.code}-{version}', root_batch_id=batch.id, reason=data.reason,
               status=RecallStatus.tracing.value, graph_version=version,
               frontier=json.dumps([[batch.id, []]]), visited=json.dumps([]),
               created_at=now(), updated_at=now())
    db.add(r)
    db.flush()
    return r


# ---------- 候选与封锁（幂等） ----------
def _add_candidate(db, recall: Recall, kind: str, target_id: int, code: str, path: list):
    existing = db.scalar(select(RecallCandidate).where(
        RecallCandidate.recall_id == recall.id,
        RecallCandidate.target_kind == kind,
        RecallCandidate.target_id == target_id,
    ))
    if existing is not None:
        paths = json.loads(existing.paths_json)
        if path not in paths:
            paths.append(path)
            existing.paths_json = json.dumps(paths, ensure_ascii=False)
        cand = existing
    else:
        cand = RecallCandidate(recall_id=recall.id, target_kind=kind, target_id=target_id,
                               target_code=code, status=CandidateStatus.unresolved.value,
                               paths_json=json.dumps([path], ensure_ascii=False), blocked=True)
        db.add(cand)
        db.flush()
    # 幂等补齐封锁：重新核对/中断恢复都不允许丢失封锁，重试也不会重复建
    blk = db.scalar(select(RecallBlock).where(
        RecallBlock.recall_id == recall.id,
        RecallBlock.target_kind == kind, RecallBlock.target_id == target_id))
    if blk is None:
        db.add(RecallBlock(recall_id=recall.id, target_kind=kind, target_id=target_id,
                           active=True, reason=f'召回 {recall.code} 追查命中，发布前暂停',
                           created_at=now()))
    elif not blk.active:
        # 重新核对再次命中此前已排除目标：发布前恢复暂停，留待重新核对
        blk.active = True
        blk.released_at = None
    return cand


def _downstream_batches(db, batch_id: int):
    """返回 [(down_batch_id, mix_code)]。"""
    out = []
    lines = db.scalars(select(MixLine).where(
        MixLine.batch_id == batch_id, MixLine.direction == 'in')).all()
    for line in lines:
        order = db.get(MixOrder, line.mix_order_id)
        for ol in db.scalars(select(MixLine).where(
                MixLine.mix_order_id == line.mix_order_id, MixLine.direction == 'out')):
            out.append((ol.batch_id, order.code if order else str(line.mix_order_id)))
    return out


def _collect_targets_for_batch(db, recall: Recall, batch: MaterialBatch, base_path: list):
    # 1. 有来源领用 -> 窑次 -> 复盘 -> 简报
    issues = db.scalars(select(Issue).where(Issue.batch_id == batch.id)).all()
    for iss in issues:
        kiln = db.get(KilnBatch, iss.kiln_id)
        if kiln is None:
            continue
        kiln_path = base_path + [f'领用 {iss.code}（{iss.qty}）', f'窑次 {kiln.code}']
        _add_candidate(db, recall, TargetKind.kiln.value, kiln.id, kiln.code, kiln_path)
        for rv in db.scalars(select(Review).where(Review.kiln_id == kiln.id)):
            _add_candidate(db, recall, TargetKind.review.value, rv.id, rv.code,
                           kiln_path + [f'复盘 {rv.code}'])
        bk_rows = db.scalars(select(BriefingKiln).where(BriefingKiln.kiln_id == kiln.id)).all()
        for row in bk_rows:
            bf = db.get(Briefing, row.briefing_id)
            if bf:
                _add_candidate(db, recall, TargetKind.briefing.value, bf.id, bf.code,
                               kiln_path + [f'简报 {bf.code}'])
    # 2. 缺来源待查领用：物料名称与受影响批号同名即命中，暂列待查，不能当作无影响
    pendings = db.scalars(select(Issue).where(Issue.batch_id.is_(None))).all()
    for p in pendings:
        if p.material_name == batch.name:
            _add_candidate(db, recall, TargetKind.pending_issue.value, p.id, p.code,
                           base_path + [f'缺来源待查领用 {p.code}（物料名 {p.material_name}）'])

# ---------- 分步追查（可中断恢复，每步独立提交） ----------
def trace_step(db, recall_id: int, max_expansions: int | None = None,
               crash_after: int | None = None) -> dict:
    recall = db.get(Recall, recall_id)
    if recall is None:
        raise RecallError('召回单不存在')
    if recall.status == RecallStatus.published.value:
        raise RecallError('召回单已发布，无需继续追查')
    # 关系更新使旧追溯失效
    current_version = trace_svc.get_graph_version(db)
    if current_version != recall.graph_version:
        recall.status = RecallStatus.rechecking.value
        db.commit()
        raise RecallError('配料关系已更新，旧追溯失效，请先重新核对')

    # frontier 元素为 [batch_id, 从根批号到该批号的路径前缀]
    raw_frontier = json.loads(recall.frontier)
    frontier = deque([e if isinstance(e, list) else [e, []] for e in raw_frontier])
    visited = set(json.loads(recall.visited))
    queued = {e[0] for e in frontier}
    expanded = 0
    crashed = False

    while frontier:
        if max_expansions is not None and expanded >= max_expansions:
            break
        bid, prefix = frontier.popleft()
        queued.discard(bid)
        if bid in visited:
            continue
        batch = db.get(MaterialBatch, bid)
        if batch is None:
            visited.add(bid)
            continue
        kind_label = '原料' if batch.kind == 'raw' else '混配'
        path_so_far = prefix + [f'{kind_label}批号 {batch.code}']
        # 当前批号直接命中的下游目标
        _collect_targets_for_batch(db, recall, batch, path_so_far)
        # 下游混配批号（携带完整链路径）
        for down_id, mix_code in _downstream_batches(db, bid):
            if down_id not in visited and down_id not in queued:
                frontier.append([down_id, path_so_far + [f'经混配 {mix_code}']])
                queued.add(down_id)
        visited.add(bid)
        expanded += 1
        # 先落断点再提交：中断恢复不遗漏、不重复封锁
        recall.frontier = json.dumps(list(frontier), ensure_ascii=False)
        recall.visited = json.dumps(sorted(visited))
        recall.total_processed = len(visited)
        recall.total_discovered = len(visited) + len(frontier)
        recall.updated_at = now()
        db.commit()
        if crash_after is not None and expanded >= crash_after:
            crashed = True
            break

    if not frontier and not crashed:
        recall.status = RecallStatus.ready.value
        recall.frontier = json.dumps([])
        recall.updated_at = now()
        db.commit()

    return progress(db, recall)


def progress(db, recall: Recall) -> dict:
    cands = db.scalars(select(RecallCandidate).where(RecallCandidate.recall_id == recall.id)).all()
    by_status = {}
    for c in cands:
        by_status[c.status] = by_status.get(c.status, 0) + 1
    return {
        'id': recall.id, 'code': recall.code, 'status': recall.status,
        'graph_version': recall.graph_version,
        'current_graph_version': trace_svc.get_graph_version(db),
        'stale': recall.graph_version != trace_svc.get_graph_version(db),
        'processed': recall.total_processed, 'discovered': recall.total_discovered,
        'frontier_size': len(json.loads(recall.frontier)),
        'candidates_total': len(cands), 'candidates_by_status': by_status,
        'published_at': recall.published_at,
    }


def run_to_end(recall_id: int, max_expansions: int | None = None,
               crash_after: int | None = None) -> dict:
    """供路由调用：自行开关 session，支持中断后反复重试直到完成。"""
    while True:
        with Session() as db:
            p = trace_step(db, recall_id, max_expansions=max_expansions,
                           crash_after=crash_after)
        if p['status'] in (RecallStatus.ready.value, RecallStatus.published.value):
            return p
        if p['frontier_size'] == 0:
            return p
        # crash_after 只在第一次调用注入，后续重试不再崩溃
        crash_after = None
        max_expansions = None


# ---------- 重新核对（关系更新后） ----------
def recheck(db, recall: Recall) -> Recall:
    current = trace_svc.get_graph_version(db)
    if current == recall.graph_version and recall.status != RecallStatus.rechecking.value:
        raise RecallError('追溯快照仍为最新，无需重新核对')
    # 不删除封锁：_add_candidate 会幂等补齐，保证重新核对期间门禁不出现空窗
    recall.graph_version = current
    recall.frontier = json.dumps([[recall.root_batch_id, []]])
    recall.visited = json.dumps([])
    recall.total_processed = 0
    recall.total_discovered = 0
    recall.status = RecallStatus.tracing.value
    recall.updated_at = now()
    # 旧候选的路径清空待重建；人工结论（affected/excluded）保留，新增目标默认 unresolved
    for c in db.scalars(select(RecallCandidate).where(RecallCandidate.recall_id == recall.id)).all():
        c.paths_json = json.dumps([])
        c.blocked = True
    db.flush()
    return recall


# ---------- 人工核对：标记受影响 / 排除（须留依据） ----------
def mark_candidate(db, recall: Recall, candidate_id: int, status: str, reason: str = ''):
    if recall.status == RecallStatus.published.value:
        raise RecallError('召回单已发布，结论不可更改')
    c = db.get(RecallCandidate, candidate_id)
    if c is None or c.recall_id != recall.id:
        raise RecallError('候选不存在')
    if status == CandidateStatus.excluded.value and not reason.strip():
        raise RecallError('排除候选必须填写排除依据')
    if c.target_kind == TargetKind.pending_issue.value and status == CandidateStatus.excluded.value:
        raise RecallError('缺来源待查领用不能排除：请先补登来源或确认受影响')
    c.status = status
    c.exclusion_reason = reason.strip()
    db.flush()
    return c


# ---------- 整单发布 ----------
def publish(db, recall: Recall, note: str = '') -> Recall:
    current = trace_svc.get_graph_version(db)
    if current != recall.graph_version:
        raise RecallError('配料关系已更新，请先重新核对后再发布')
    if recall.status not in (RecallStatus.ready.value, RecallStatus.rechecking.value):
        raise RecallError('追查尚未完成，不能发布')
    cands = db.scalars(select(RecallCandidate).where(RecallCandidate.recall_id == recall.id)).all()
    unresolved = [c for c in cands if c.status == CandidateStatus.unresolved.value]
    if unresolved:
        raise RecallError(
            '仍有 ' + str(len(unresolved)) + ' 项未核对（含缺来源待查领用），不能发布')
    for c in cands:
        blk = db.scalar(select(RecallBlock).where(
            RecallBlock.recall_id == recall.id,
            RecallBlock.target_kind == c.target_kind,
            RecallBlock.target_id == c.target_id))
        if c.status == CandidateStatus.affected.value:
            # 受影响：继续封锁
            if blk is not None and not blk.active:
                blk.active = True
                blk.released_at = None
            c.blocked = True
        else:
            # 排除：解除封锁并留排除依据
            if blk is not None:
                blk.active = False
                blk.reason = f'已排除：{c.exclusion_reason}'
                blk.released_at = now()
            c.blocked = False
    recall.status = RecallStatus.published.value
    recall.published_at = now()
    recall.updated_at = now()
    db.flush()
    return recall


# ---------- 详情 ----------
def recall_detail(db, recall: Recall) -> dict:
    base = progress(db, recall)
    root = db.get(MaterialBatch, recall.root_batch_id)
    base['root_batch'] = trace_svc.batch_dict(root) if root else None
    base['reason'] = recall.reason
    base['note'] = ''
    cands = []
    for c in db.scalars(select(RecallCandidate).where(
            RecallCandidate.recall_id == recall.id).order_by(RecallCandidate.id)):
        cands.append({
            'id': c.id, 'target_kind': c.target_kind, 'target_id': c.target_id,
            'target_code': c.target_code, 'status': c.status,
            'paths': json.loads(c.paths_json),
            'exclusion_reason': c.exclusion_reason, 'blocked': c.blocked,
        })
    base['candidates'] = cands
    return base
