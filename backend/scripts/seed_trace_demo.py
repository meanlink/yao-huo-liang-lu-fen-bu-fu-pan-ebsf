"""釉料追溯演示数据：幂等 seed，自然键防重复，可反复执行。

运行：python -m scripts.seed_trace_demo
"""
import json
import time

from sqlalchemy import select, func

from src.config.database import Session
from migrations.initial import migrate
from src.models import MaterialBatch, KilnBatch, Audit
from src.schemas.trace import (
    RawBatchIn, MixOrderIn, MixLineIn, KilnIn, LinkedIssueIn,
    PendingIssueIn, ReviewIn, BriefingIn,
)
from src.services import trace as svc

BASE_TS = 1763000000


def seed():
    migrate()
    with Session() as db:
        if db.scalar(select(MaterialBatch).where(MaterialBatch.code == 'RM-FELDSPAR')):
            print(json.dumps({'trace_seed': 'skipped, already present'}, ensure_ascii=False))
            return

        # 原料
        svc.register_raw(db, RawBatchIn(code='RM-FELDSPAR', name='长石粉', supplier='甲矿', qty=200))
        svc.register_raw(db, RawBatchIn(code='RM-QUARTZ', name='石英粉', supplier='乙矿', qty=160))
        svc.register_raw(db, RawBatchIn(code='RM-KAOLIN', name='高岭土', supplier='丙矿', qty=120))

        # 多级混配：基础釉 -> 哑光釉 -> 拆分为 A/B 两批
        svc.create_mix(db, MixOrderIn(
            code='MIX-BASE', loss_qty=5,
            inputs=[MixLineIn(code='RM-FELDSPAR', qty=80), MixLineIn(code='RM-QUARTZ', qty=40)],
            outputs=[MixLineIn(code='GLAZE-BASE', qty=115)]))
        svc.create_mix(db, MixOrderIn(
            code='MIX-MAT',
            inputs=[MixLineIn(code='GLAZE-BASE', qty=40), MixLineIn(code='RM-KAOLIN', qty=20)],
            outputs=[MixLineIn(code='GLAZE-MAT', qty=60)]))
        svc.create_mix(db, MixOrderIn(
            code='MIX-SPLIT',
            inputs=[MixLineIn(code='GLAZE-MAT', qty=20)],
            outputs=[MixLineIn(code='GLAZE-MAT-A', qty=12), MixLineIn(code='GLAZE-MAT-B', qty=8)]))

        # 窑次
        for i, code in enumerate(['QH-101', 'QH-102', 'QH-103', 'QH-104']):
            svc.register_kiln(db, KilnIn(code=code, line=f'L{(i % 3) + 1}', fired=120,
                                         passed=108 - i, fired_at=BASE_TS + i * 3600))

        # 领用（含一笔缺来源待查）
        svc.create_issue(db, LinkedIssueIn(code='IS-101', batch_code='GLAZE-MAT-A', qty=5, kiln_code='QH-101'))
        svc.create_issue(db, LinkedIssueIn(code='IS-102', batch_code='GLAZE-MAT-B', qty=3, kiln_code='QH-102'))
        svc.create_issue(db, LinkedIssueIn(code='IS-103', batch_code='GLAZE-BASE', qty=10, kiln_code='QH-103'))
        svc.create_pending_issue(db, PendingIssueIn(
            code='IS-104-PEND', material_name='GLAZE-MAT', qty=2, kiln_code='QH-104'))

        # 复盘与简报
        for i, code in enumerate(['QH-101', 'QH-102', 'QH-103', 'QH-104']):
            svc.register_review(db, ReviewIn(
                code=f'RV-{code[3:]}', kiln_code=code,
                yield_rate=round((108 - i) / 120 * 100, 2), note='周度良率复盘'))
        svc.register_briefing(db, BriefingIn(
            code='BF-W46', title='第 46 周班组复核简报',
            content='关注哑光釉领用窑次的低尾良率。', kiln_codes=['QH-101', 'QH-102']))

        db.add(Audit(actor='seed', action='trace.demo.seed', detail={}, at=time.time()))
        db.commit()
        counts = {
            'material_batches': db.scalar(select(func.count()).select_from(MaterialBatch)),
            'kilns': db.scalar(select(func.count()).select_from(KilnBatch)),
        }
        print(json.dumps(counts, ensure_ascii=False))


if __name__ == '__main__':
    seed()
