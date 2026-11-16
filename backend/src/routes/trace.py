from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.config.database import get_db
from src.middlewares.auth import current_user, can_write
from src.models import (
    MaterialBatch, MixOrder, MixLine, Issue, KilnBatch, Review, Briefing, BriefingKiln,
    StockLedger, Recall,
)
from src.schemas.trace import (
    RawBatchIn, MixOrderIn, KilnIn, LinkedIssueIn, PendingIssueIn, IssueResolveIn,
    ReviewIn, BriefingIn, RecallIn, ExclusionIn, PublishIn,
)
from src.services import trace as svc
from src.services import recall as recall_svc
from src.services.audit import record

router = APIRouter(prefix='/trace')


def _err(e: Exception):
    return HTTPException(400, str(e))


# ---------------- 原料 ----------------
@router.get('/materials')
def list_materials(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(MaterialBatch).order_by(MaterialBatch.id.desc())).all()
    return [svc.batch_dict(b) for b in rows]


@router.post('/materials/raw', status_code=201)
def create_raw(data: RawBatchIn, db=Depends(get_db), u=Depends(can_write)):
    try:
        b = svc.register_raw(db, data)
        record(db, u.email, 'trace.raw.register', {'code': b.code, 'qty': data.qty})
        db.commit()
    except svc.TraceError as e:
        db.rollback()
        raise _err(e)
    return svc.batch_dict(b)


@router.get('/materials/{code}/genealogy')
def genealogy(code: str, db=Depends(get_db), u=Depends(current_user)):
    try:
        return svc.genealogy(db, code)
    except svc.TraceError as e:
        raise _err(e)


# ---------------- 混配 ----------------
@router.post('/mixes', status_code=201)
def create_mix(data: MixOrderIn, db=Depends(get_db), u=Depends(can_write)):
    try:
        order = svc.create_mix(db, data)
        record(db, u.email, 'trace.mix.create', {'code': order.code})
        db.commit()
        db.refresh(order)
    except svc.TraceError as e:
        db.rollback()
        raise _err(e)
    return svc.genealogy(db, data.outputs[0].code)


@router.get('/mixes')
def list_mixes(db=Depends(get_db), u=Depends(current_user)):
    orders = db.scalars(select(MixOrder).order_by(MixOrder.id.desc())).all()
    out = []
    for o in orders:
        lines = db.scalars(select(MixLine).where(MixLine.mix_order_id == o.id)).all()
        def line_item(l):
            b = db.get(MaterialBatch, l.batch_id)
            return {'batch_id': l.batch_id, 'batch_code': b.code if b else str(l.batch_id), 'qty': l.qty}
        out.append({
            'id': o.id, 'code': o.code, 'note': o.note, 'loss_qty': o.loss_qty,
            'graph_version': o.graph_version,
            'inputs': [line_item(l) for l in lines if l.direction == 'in'],
            'outputs': [line_item(l) for l in lines if l.direction == 'out'],
        })
    return out


# ---------------- 窑次 / 复盘 / 简报 ----------------
@router.post('/kilns', status_code=201)
def create_kiln(data: KilnIn, db=Depends(get_db), u=Depends(can_write)):
    try:
        k = svc.register_kiln(db, data)
        record(db, u.email, 'trace.kiln.register', {'code': k.code})
        db.commit()
    except svc.TraceError as e:
        db.rollback()
        raise _err(e)
    return svc.kiln_dict(k)


@router.get('/kilns')
def list_kilns(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(KilnBatch).order_by(KilnBatch.id.desc())).all()
    return [svc.kiln_dict(k) for k in rows]


@router.post('/reviews', status_code=201)
def create_review(data: ReviewIn, db=Depends(get_db), u=Depends(can_write)):
    try:
        r = svc.register_review(db, data)
        record(db, u.email, 'trace.review.register', {'code': r.code})
        db.commit()
    except svc.TraceError as e:
        db.rollback()
        raise _err(e)
    return {'id': r.id, 'code': r.code, 'kiln_id': r.kiln_id,
            'yield_rate': r.yield_rate, 'note': r.note}


@router.get('/reviews')
def list_reviews(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(Review).order_by(Review.id.desc())).all()
    result = []
    for r in rows:
        k = db.get(KilnBatch, r.kiln_id)
        blk = recall_svc.review_blocked(db, r.id)
        result.append({'id': r.id, 'code': r.code, 'kiln_code': k.code if k else '',
                       'yield_rate': r.yield_rate, 'note': r.note,
                       'blocked': blk is not None,
                       'block_reason': blk.reason if blk else ''})
    return result


@router.post('/reviews/{rid}/approve')
def approve_review(rid: int, db=Depends(get_db), u=Depends(can_write)):
    try:
        recall_svc.ensure_unblocked(db, 'review', rid, '复盘批准')
        record(db, u.email, 'trace.review.approve', {'id': rid})
        db.commit()
    except recall_svc.RecallError as e:
        db.rollback()
        raise _err(e)
    return {'id': rid, 'approved': True}


@router.post('/briefings', status_code=201)
def create_briefing(data: BriefingIn, db=Depends(get_db), u=Depends(can_write)):
    try:
        b = svc.register_briefing(db, data)
        record(db, u.email, 'trace.briefing.register', {'code': b.code})
        db.commit()
    except svc.TraceError as e:
        db.rollback()
        raise _err(e)
    return {'id': b.id, 'code': b.code, 'title': b.title}


@router.get('/briefings')
def list_briefings(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(Briefing).order_by(Briefing.id.desc())).all()
    out = []
    for b in rows:
        kiln_codes = []
        for lk in db.scalars(select(BriefingKiln).where(BriefingKiln.briefing_id == b.id)):
            k = db.get(KilnBatch, lk.kiln_id)
            if k:
                kiln_codes.append(k.code)
        blk = recall_svc.briefing_blocked(db, b.id)
        out.append({'id': b.id, 'code': b.code, 'title': b.title,
                    'content': b.content, 'kiln_codes': kiln_codes,
                    'blocked': blk is not None, 'block_reason': blk.reason if blk else ''})
    return out


@router.get('/briefings/{bid}/download')
def download_briefing(bid: int, db=Depends(get_db), u=Depends(current_user)):
    b = db.get(Briefing, bid)
    if b is None:
        raise HTTPException(404, '简报不存在')
    blk = recall_svc.briefing_blocked(db, bid)
    if blk is not None:
        raise HTTPException(409, f'下载已暂停：{blk.reason}')
    from fastapi.responses import PlainTextResponse
    lines = [f'简报 {b.code} · {b.title}', '', b.content, '', '关联窑次：' +
             ', '.join((db.get(KilnBatch, lk.kiln_id).code or '?')
                       for lk in db.scalars(select(BriefingKiln).where(BriefingKiln.briefing_id == bid)))]
    return PlainTextResponse('\n'.join(lines),
                             headers={'Content-Disposition': f'attachment; filename="briefing-{b.code}.txt"'})


# ---------------- 领用 ----------------
@router.post('/issues', status_code=201)
def create_issue(data: LinkedIssueIn, db=Depends(get_db), u=Depends(can_write)):
    try:
        iss = svc.create_issue(db, data)
        record(db, u.email, 'trace.issue.create', {'code': iss.code})
        db.commit()
    except svc.TraceError as e:
        db.rollback()
        raise _err(e)
    k = db.get(KilnBatch, iss.kiln_id)
    return svc.issue_dict(iss, k.code if k else '')


@router.post('/issues/pending', status_code=201)
def create_pending(data: PendingIssueIn, db=Depends(get_db), u=Depends(can_write)):
    try:
        iss = svc.create_pending_issue(db, data)
        record(db, u.email, 'trace.issue.pending', {'code': iss.code})
        db.commit()
    except svc.TraceError as e:
        db.rollback()
        raise _err(e)
    k = db.get(KilnBatch, iss.kiln_id)
    return svc.issue_dict(iss, k.code if k else '')


@router.post('/issues/{iid}/resolve')
def resolve_issue(iid: int, data: IssueResolveIn, db=Depends(get_db), u=Depends(can_write)):
    try:
        iss = svc.resolve_pending_issue(db, iid, data)
        record(db, u.email, 'trace.issue.resolve', {'id': iid, 'batch': data.batch_code})
        db.commit()
    except svc.TraceError as e:
        db.rollback()
        raise _err(e)
    k = db.get(KilnBatch, iss.kiln_id)
    return svc.issue_dict(iss, k.code if k else '')


@router.get('/issues')
def list_issues(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(Issue).order_by(Issue.id.desc())).all()
    out = []
    for i in rows:
        k = db.get(KilnBatch, i.kiln_id)
        blk = recall_svc.issue_is_blocked(db, i.id) if i.status == 'pending' else False
        out.append({**svc.issue_dict(i, k.code if k else ''), 'blocked': blk})
    return out


# ---------------- 余量流水 ----------------
@router.get('/materials/{code}/ledger')
def material_ledger(code: str, db=Depends(get_db), u=Depends(current_user)):
    b = db.scalar(select(MaterialBatch).where(MaterialBatch.code == code))
    if b is None:
        raise HTTPException(404, '批号不存在')
    rows = db.scalars(select(StockLedger).where(StockLedger.batch_id == b.id)
                      .order_by(StockLedger.id)).all()
    return [{'change_qty': r.change_qty, 'balance_after': r.balance_after,
             'reason': r.reason, 'ref_code': r.ref_code, 'created_at': r.created_at} for r in rows]


# ---------------- 召回 ----------------
@router.post('/recalls', status_code=201)
def create_recall(data: RecallIn, db=Depends(get_db), u=Depends(can_write)):
    try:
        r = recall_svc.create_recall(db, data)
        record(db, u.email, 'trace.recall.create', {'code': r.code, 'batch': data.batch_code})
        db.commit()
    except recall_svc.RecallError as e:
        db.rollback()
        raise _err(e)
    return {'id': r.id, 'code': r.code, 'status': r.status}


@router.post('/recalls/{rid}/trace')
def trace_recall(rid: int, max_expansions: int | None = None,
                 crash_after: int | None = None, db=Depends(get_db), u=Depends(can_write)):
    try:
        p = recall_svc.trace_step(db, rid, max_expansions=max_expansions,
                                  crash_after=crash_after)
        record(db, u.email, 'trace.recall.step', {'id': rid, 'processed': p['processed']})
        db.commit()
    except recall_svc.RecallError as e:
        db.rollback()
        raise _err(e)
    return p


@router.post('/recalls/{rid}/recheck')
def recheck_recall(rid: int, db=Depends(get_db), u=Depends(can_write)):
    recall = db.get(Recall, rid)
    if recall is None:
        raise HTTPException(404, '召回单不存在')
    try:
        recall_svc.recheck(db, recall)
        record(db, u.email, 'trace.recall.recheck', {'id': rid})
        db.commit()
        p = recall_svc.trace_step(db, rid)
        db.commit()
    except recall_svc.RecallError as e:
        db.rollback()
        raise _err(e)
    return p


@router.get('/recalls')
def list_recalls(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(Recall).order_by(Recall.id.desc())).all()
    return [recall_svc.progress(db, r) for r in rows]


@router.get('/recalls/{rid}')
def recall_detail(rid: int, db=Depends(get_db), u=Depends(current_user)):
    r = db.get(Recall, rid)
    if r is None:
        raise HTTPException(404, '召回单不存在')
    return recall_svc.recall_detail(db, r)


@router.post('/recalls/{rid}/candidates/{cid}/exclude')
def exclude_candidate(rid: int, cid: int, data: ExclusionIn,
                      db=Depends(get_db), u=Depends(can_write)):
    r = db.get(Recall, rid)
    if r is None:
        raise HTTPException(404, '召回单不存在')
    try:
        recall_svc.mark_candidate(db, r, cid, 'excluded', data.reason)
        record(db, u.email, 'trace.recall.exclude', {'recall': rid, 'candidate': cid})
        db.commit()
    except recall_svc.RecallError as e:
        db.rollback()
        raise _err(e)
    return recall_svc.recall_detail(db, r)


@router.post('/recalls/{rid}/candidates/{cid}/affected')
def mark_affected(rid: int, cid: int, db=Depends(get_db), u=Depends(can_write)):
    r = db.get(Recall, rid)
    if r is None:
        raise HTTPException(404, '召回单不存在')
    try:
        recall_svc.mark_candidate(db, r, cid, 'affected')
        record(db, u.email, 'trace.recall.affected', {'recall': rid, 'candidate': cid})
        db.commit()
    except recall_svc.RecallError as e:
        db.rollback()
        raise _err(e)
    return recall_svc.recall_detail(db, r)


@router.post('/recalls/{rid}/publish')
def publish_recall(rid: int, data: PublishIn, db=Depends(get_db), u=Depends(can_write)):
    r = db.get(Recall, rid)
    if r is None:
        raise HTTPException(404, '召回单不存在')
    try:
        recall_svc.publish(db, r, data.note)
        record(db, u.email, 'trace.recall.publish', {'id': rid})
        db.commit()
    except recall_svc.RecallError as e:
        db.rollback()
        raise _err(e)
    return recall_svc.recall_detail(db, r)
