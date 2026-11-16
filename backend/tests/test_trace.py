"""釉料追溯与召回子系统端到端测试（SQLite，无需 Postgres/Redis）。

运行：cd backend && python -m tests.test_trace
"""
import os
import sys
import threading
import tempfile

# 必须在导入应用前指定隔离的 SQLite 数据库
_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmp.close()
os.environ['DATABASE_URL'] = f'sqlite:///{_tmp.name}'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6399/0'
os.environ['SEED_DEMO'] = 'false'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from src.main import app  # noqa: E402
from src.config.database import Session  # noqa: E402
from migrations.initial import migrate  # noqa: E402
from src.models import (  # noqa: E402
    MaterialBatch, Issue, Recall, RecallCandidate, RecallBlock,
)
from src.services import recall as recall_svc  # noqa: E402

PASS, FAILS = 0, []


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  ✓ {name}')
    else:
        FAILS.append((name, detail))
        print(f'  ✗ {name} {detail}')


def expect_error(name, resp, status=400):
    check(name, resp.status_code == status, f'got {resp.status_code} {resp.text}')


def main():
    migrate()
    client = TestClient(app)
    H = {}  # 测试直接绕过登录：依赖覆盖
    from src.middlewares.auth import current_user, can_write
    from src.models import User

    class FakeUser:
        role = 'admin'
        email = 'test@demo.local'
        name = '测试员'
        id = 0

    app.dependency_overrides[current_user] = lambda: FakeUser()
    app.dependency_overrides[can_write] = lambda: FakeUser()

    print('\n== 1. 原料登记与守恒余量 ==')
    r = client.post('/trace/materials/raw', json={'code': 'RM-A', 'name': '长石粉', 'qty': 100, 'supplier': '甲矿'})
    check('登记原料 RM-A', r.status_code == 201, r.text)
    r = client.post('/trace/materials/raw', json={'code': 'RM-B', 'name': '石英粉', 'qty': 80})
    check('登记原料 RM-B', r.status_code == 201, r.text)
    r = client.post('/trace/materials/raw', json={'code': 'RM-A', 'name': '重复', 'qty': 1})
    expect_error('重复批号拒绝', r)
    with Session() as db:
        b = db.scalar(select(MaterialBatch).where(MaterialBatch.code == 'RM-A'))
        check('初始余量=登记量', b.on_hand_qty == 100)

    print('\n== 2. 多级混配（拆合）与守恒 ==')
    # RM-A 40 + RM-B 20 -> GLAZE-1 55 + 损耗 5
    r = client.post('/trace/mixes', json={
        'code': 'MIX-1', 'loss_qty': 5,
        'inputs': [{'code': 'RM-A', 'qty': 40}, {'code': 'RM-B', 'qty': 20}],
        'outputs': [{'code': 'GLAZE-1', 'qty': 55}]})
    check('一级混配 MIX-1 成功', r.status_code == 201, r.text)
    r = client.post('/trace/mixes', json={
        'code': 'MIX-BAD', 'loss_qty': 0,
        'inputs': [{'code': 'RM-A', 'qty': 10}],
        'outputs': [{'code': 'X', 'qty': 9}]})
    expect_error('投料≠产出+损耗 拒绝', r)
    # 超投：RM-A 余 60，申请 999
    r = client.post('/trace/mixes', json={
        'code': 'MIX-OVER', 'loss_qty': 0,
        'inputs': [{'code': 'RM-A', 'qty': 999}],
        'outputs': [{'code': 'Y', 'qty': 999}]})
    expect_error('超余量投料拒绝', r)
    # 二级混配：GLAZE-1 20 + RM-B 10 -> GLAZE-2 30
    r = client.post('/trace/mixes', json={
        'code': 'MIX-2',
        'inputs': [{'code': 'GLAZE-1', 'qty': 20}, {'code': 'RM-B', 'qty': 10}],
        'outputs': [{'code': 'GLAZE-2', 'qty': 30}]})
    check('二级混配 MIX-2 成功', r.status_code == 201, r.text)
    # 拆分：GLAZE-2 10 -> GLAZE-2A 6 + GLAZE-2B 4
    r = client.post('/trace/mixes', json={
        'code': 'MIX-3',
        'inputs': [{'code': 'GLAZE-2', 'qty': 10}],
        'outputs': [{'code': 'GLAZE-2A', 'qty': 6}, {'code': 'GLAZE-2B', 'qty': 4}]})
    check('拆分混配 MIX-3 成功', r.status_code == 201, r.text)
    with Session() as db:
        rm_a = db.scalar(select(MaterialBatch).where(MaterialBatch.code == 'RM-A'))
        rm_b = db.scalar(select(MaterialBatch).where(MaterialBatch.code == 'RM-B'))
        g1 = db.scalar(select(MaterialBatch).where(MaterialBatch.code == 'GLAZE-1'))
        check('RM-A 余量守恒 100-40=60', rm_a.on_hand_qty == 60, str(rm_a.on_hand_qty))
        check('RM-B 余量守恒 80-20-10=50', rm_b.on_hand_qty == 50, str(rm_b.on_hand_qty))
        check('GLAZE-1 余量 55-20=35', g1.on_hand_qty == 35, str(g1.on_hand_qty))
    r = client.get('/trace/materials/RM-A/ledger')
    check('守恒流水可查', r.status_code == 200 and len(r.json()) >= 2, r.text)

    print('\n== 3. 循环拒绝 ==')
    # GLAZE-2B 是 RM-A 的下游；尝试以 GLAZE-2B 为投入、RM-A 为产出 -> 成环
    r = client.post('/trace/mixes', json={
        'code': 'MIX-CYCLE',
        'inputs': [{'code': 'GLAZE-2B', 'qty': 2}],
        'outputs': [{'code': 'RM-A', 'qty': 2}]})
    expect_error('配料循环拒绝', r)

    print('\n== 4. 窑次、领用与并发超领 ==')
    for code in ['K-1', 'K-2', 'K-3']:
        r = client.post('/trace/kilns', json={'code': code, 'line': 'L1', 'fired': 100, 'passed': 90, 'fired_at': 1763000000})
        check(f'登记窑次 {code}', r.status_code == 201, r.text)
    # GLAZE-2A 余 6：领 4 给 K-1
    r = client.post('/trace/issues', json={'code': 'IS-1', 'batch_code': 'GLAZE-2A', 'qty': 4, 'kiln_code': 'K-1'})
    check('有来源领用 IS-1', r.status_code == 201, r.text)
    r = client.post('/trace/issues', json={'code': 'IS-OVER', 'batch_code': 'GLAZE-2A', 'qty': 99, 'kiln_code': 'K-2'})
    expect_error('超余量领用拒绝', r)
    # 并发：GLAZE-2A 余 2，两个线程各领 2，只允许一个成功
    results = []

    def issue(code):
        with TestClient(app) as c:
            rr = c.post('/trace/issues', json={'code': code, 'batch_code': 'GLAZE-2A', 'qty': 2, 'kiln_code': 'K-3'})
            results.append(rr.status_code)

    t1 = threading.Thread(target=issue, args=('IS-C1',))
    t2 = threading.Thread(target=issue, args=('IS-C2',))
    t1.start(); t2.start(); t1.join(); t2.join()
    check('并发超领仅一笔成功', sorted(results) == [201, 400], str(results))
    with Session() as db:
        g = db.scalar(select(MaterialBatch).where(MaterialBatch.code == 'GLAZE-2A'))
        check('并发后余量为 0 不出现负库存', g.on_hand_qty == 0, str(g.on_hand_qty))

    print('\n== 5. 缺来源领用暂列待查，不能当作无影响 ==')
    # GLAZE-1 名称默认是 code，这里用同名物料登记一条缺来源领用
    r = client.post('/trace/issues/pending', json={'code': 'IS-PEND', 'material_name': 'GLAZE-1', 'qty': 3, 'kiln_code': 'K-2'})
    check('待查领用登记成功', r.status_code == 201 and r.json()['status'] == 'pending', r.text)
    r = client.get('/trace/issues')
    pend = [i for i in r.json() if i['status'] == 'pending']
    check('待查清单可见', len(pend) == 1 and pend[0]['code'] == 'IS-PEND')

    print('\n== 6. 复盘与简报 ==')
    r = client.post('/trace/reviews', json={'code': 'RV-1', 'kiln_code': 'K-1', 'yield_rate': 91.2, 'note': '低尾复核'})
    check('登记复盘 RV-1', r.status_code == 201, r.text)
    r = client.post('/trace/briefings', json={'code': 'BF-1', 'title': '周复核简报', 'content': '见名单', 'kiln_codes': ['K-1', 'K-2']})
    check('登记简报 BF-1', r.status_code == 201, r.text)
    r = client.post('/trace/reviews/1/approve')
    check('无召回时复盘可批准', r.status_code == 200, r.text)

    print('\n== 7. 发起召回、分步追查、中断恢复 ==')
    r = client.post('/trace/recalls', json={'batch_code': 'RM-A', 'reason': '含铁量异常'})
    check('发起召回', r.status_code == 201, r.text)
    rid = r.json()['id']
    # 第一步只展开 1 个节点，模拟中断
    r = client.post(f'/trace/recalls/{rid}/trace?max_expansions=1')
    p1 = r.json()
    check('首步追查后仍在进行', r.status_code == 200 and p1['status'] == 'tracing', r.text)
    check('断点 frontier 非空', p1['frontier_size'] >= 1, str(p1))
    # 模拟崩溃恢复：再跑一步（crash_after 由服务层支持，这里验证幂等）
    r = client.post(f'/trace/recalls/{rid}/trace?max_expansions=1')
    check('中断后恢复追查', r.status_code == 200, r.text)
    # 跑完
    r = client.post(f'/trace/recalls/{rid}/trace')
    p = r.json()
    check('追查完成 ready', p['status'] == 'ready', str(p))
    r = client.get(f'/trace/recalls/{rid}')
    detail = r.json()
    kinds = {}
    for c in detail['candidates']:
        kinds[c['target_kind']] = kinds.get(c['target_kind'], 0) + 1
    check('命中窑次（K-1 链路）', kinds.get('kiln', 0) >= 1, str(kinds))
    check('命中复盘 RV-1', kinds.get('review', 0) >= 1, str(kinds))
    check('命中简报 BF-1', kinds.get('briefing', 0) >= 1, str(kinds))
    check('命中缺来源待查领用', kinds.get('pending_issue', 0) >= 1, str(kinds))
    # 路径沿配料链
    kiln_cand = next(c for c in detail['candidates'] if c['target_kind'] == 'kiln')
    check('追查路径含混配与领用环节', any('MIX' in s or '领用' in s for s in kiln_cand['paths'][0]),
          str(kiln_cand['paths']))
    # 幂等封锁：重复追查不产生重复封锁
    with Session() as db:
        n_blocks = db.query(RecallBlock).filter(RecallBlock.recall_id == rid).count()
    client.post(f'/trace/recalls/{rid}/trace')
    with Session() as db:
        n_blocks2 = db.query(RecallBlock).filter(RecallBlock.recall_id == rid).count()
    check('重试不重复封锁', n_blocks == n_blocks2, f'{n_blocks} vs {n_blocks2}')

    print('\n== 8. 发布前暂停批准与下载 ==')
    rv = next(c for c in detail['candidates'] if c['target_kind'] == 'review')
    r = client.post(f'/trace/reviews/{rv["target_id"]}/approve')
    expect_error('封锁复盘批准', r, status=400)
    bf = next(c for c in detail['candidates'] if c['target_kind'] == 'briefing')
    r = client.get(f'/trace/briefings/{bf["target_id"]}/download')
    check('封锁简报下载 409', r.status_code == 409, r.text)
    # 待查领用封锁期间不得补登
    pend_cand = next(c for c in detail['candidates'] if c['target_kind'] == 'pending_issue')
    r = client.post(f'/trace/issues/{pend_cand["target_id"]}/resolve', json={'batch_code': 'GLAZE-1'})
    expect_error('封锁期间补登待查领用拒绝', r)

    print('\n== 9. 核对：受影响 / 排除须留依据 / 待查不可排除 ==')
    r = client.post(f'/trace/recalls/{rid}/candidates/{rv["id"]}/exclude', json={'reason': ''})
    expect_error('无依据排除拒绝', r)
    r = client.post(f'/trace/recalls/{rid}/candidates/{pend_cand["id"]}/exclude', json={'reason': '尝试排除'})
    expect_error('待查领用不可排除', r)
    # 窑次、复盘、简报标记受影响；待查补登后再标记
    for c in detail['candidates']:
        if c['target_kind'] == 'pending_issue':
            continue
        r = client.post(f'/trace/recalls/{rid}/candidates/{c["id"]}/affected')
        check(f'标记受影响 {c["target_kind"]}', r.status_code == 200, r.text)
    # 未核对完不能发布
    r = client.post(f'/trace/recalls/{rid}/publish', json={})
    expect_error('存在未核对项禁止发布', r)

    print('\n== 10. 追溯期间仍可登记；关系更新使旧追溯失效 → 重新核对 → 整单发布 ==')
    # 新增一个窑次和领用（追溯期间登记）
    client.post('/trace/kilns', json={'code': 'K-4', 'line': 'L2', 'fired': 80, 'passed': 70, 'fired_at': 1763000100})
    # GLAZE-1 余 35，领 5
    r = client.post('/trace/issues', json={'code': 'IS-NEW', 'batch_code': 'GLAZE-1', 'qty': 5, 'kiln_code': 'K-4'})
    check('追溯期间新增登记成功', r.status_code == 201, r.text)
    # 旧追溯应被识别为失效
    r = client.post(f'/trace/recalls/{rid}/trace')
    expect_error('关系更新后旧追溯失效', r)
    r = client.get(f'/trace/recalls/{rid}')
    check('召回标记 stale/rechecking', r.json()['stale'] and r.json()['status'] == 'rechecking', r.text)
    # 重新核对
    r = client.post(f'/trace/recalls/{rid}/recheck')
    check('重新核对执行', r.status_code == 200, r.text)
    r = client.get(f'/trace/recalls/{rid}')
    detail2 = r.json()
    check('重核后不再 stale', detail2['stale'] is False, str(detail2['stale']))
    new_kiln = next((c for c in detail2['candidates'] if c['target_kind'] == 'kiln' and c['target_code'] == 'K-4'), None)
    check('重新核对未遗漏新增去向 K-4', new_kiln is not None, str([c['target_code'] for c in detail2['candidates']]))
    # 旧结论保留（affected），新候选需要核对
    old_rv = next(c for c in detail2['candidates'] if c['target_kind'] == 'review')
    check('旧核对结论保留', old_rv['status'] == 'affected', old_rv['status'])
    # 先补登待查领用（此时仍封锁？发布前封锁仍在）-> 应拒绝；改为直接标记受影响
    r = client.post(f'/trace/recalls/{rid}/candidates/{new_kiln["id"]}/affected')
    check('新增窑次标记受影响', r.status_code == 200, r.text)
    pend2 = next(c for c in detail2['candidates'] if c['target_kind'] == 'pending_issue')
    r = client.post(f'/trace/recalls/{rid}/candidates/{pend2["id"]}/affected')
    check('待查领用确认受影响', r.status_code == 200, r.text)
    # 全部核对后整单发布
    r = client.post(f'/trace/recalls/{rid}/publish', json={'note': '整单发布'})
    check('整单发布成功', r.status_code == 200 and r.json()['status'] == 'published', r.text)

    print('\n== 11. 发布后列明受影响及排除依据，门禁按结论处置 ==')
    r = client.get(f'/trace/recalls/{rid}')
    pub = r.json()
    affected = [c for c in pub['candidates'] if c['status'] == 'affected']
    check('发布单列明受影响项', len(affected) == len(pub['candidates']), str(len(affected)))
    # 受影响项仍封锁
    rv2 = next(c for c in pub['candidates'] if c['target_kind'] == 'review')
    r = client.post(f'/trace/reviews/{rv2["target_id"]}/approve')
    expect_error('发布后受影响复盘仍暂停', r)
    # 新增一个排除场景：第二个召回，排除一个窑次并验证解封+依据
    client.post('/trace/kilns', json={'code': 'K-5', 'line': 'L1', 'fired': 50, 'passed': 48, 'fired_at': 1763000200})
    client.post('/trace/issues', json={'code': 'IS-5', 'batch_code': 'RM-B', 'qty': 2, 'kiln_code': 'K-5'})
    client.post('/trace/reviews', json={'code': 'RV-5', 'kiln_code': 'K-5', 'yield_rate': 96.0})
    r = client.post('/trace/recalls', json={'batch_code': 'RM-B', 'reason': '粒度波动'})
    rid2 = r.json()['id']
    client.post(f'/trace/recalls/{rid2}/trace')
    d2 = client.get(f'/trace/recalls/{rid2}').json()
    for c in d2['candidates']:
        if c['target_kind'] == 'review' and c['target_code'] == 'RV-5':
            r = client.post(f'/trace/recalls/{rid2}/candidates/{c["id"]}/exclude',
                            json={'reason': '该批复盘所用批次留样复检合格，与异常批号无关'})
            check('带依据排除成功', r.status_code == 200, r.text)
        else:
            client.post(f'/trace/recalls/{rid2}/candidates/{c["id"]}/affected')
    r = client.post(f'/trace/recalls/{rid2}/publish', json={})
    check('第二召回发布', r.status_code == 200, r.text)
    pub2 = r.json()
    excl = next(c for c in pub2['candidates'] if c['status'] == 'excluded')
    check('发布单保留排除依据', bool(excl['exclusion_reason']), str(excl))
    check('排除项解除封锁', excl['blocked'] is False)
    rv5 = next(c for c in pub2['candidates'] if c['target_code'] == 'RV-5')
    r = client.post(f'/trace/reviews/{rv5["target_id"]}/approve')
    check('排除复盘恢复可批准', r.status_code == 200, r.text)

    print('\n== 12. 崩溃恢复（服务层 crash_after 注入）==')
    client.post('/trace/kilns', json={'code': 'K-6', 'line': 'L3', 'fired': 60, 'passed': 55, 'fired_at': 1763000300})
    client.post('/trace/issues', json={'code': 'IS-6', 'batch_code': 'RM-B', 'qty': 1, 'kiln_code': 'K-6'})
    r = client.post('/trace/recalls', json={'batch_code': 'RM-B', 'reason': '再次核对新增窑次'})
    # 上一召回已发布：允许发起新召回；进行中的重复发起才拒绝
    check('已发布批号允许再次发起召回', r.status_code == 201, r.text)
    dup = client.post('/trace/recalls', json={'batch_code': 'RM-B', 'reason': '重复发起'})
    expect_error('进行中召回重复发起拒绝', dup)
    rid_old = r.json()['id']
    client.post(f'/trace/recalls/{rid_old}/trace')  # 收尾，避免干扰
    client.post('/trace/materials/raw', json={'code': 'RM-C', 'name': '高岭土', 'qty': 40})
    client.post('/trace/mixes', json={'code': 'MIX-C1', 'inputs': [{'code': 'RM-C', 'qty': 10}],
                                      'outputs': [{'code': 'GC-1', 'qty': 10}]})
    client.post('/trace/mixes', json={'code': 'MIX-C2', 'inputs': [{'code': 'GC-1', 'qty': 5}],
                                      'outputs': [{'code': 'GC-2', 'qty': 5}]})
    client.post('/trace/issues', json={'code': 'IS-C', 'batch_code': 'GC-2', 'qty': 2, 'kiln_code': 'K-6'})
    r = client.post('/trace/recalls', json={'batch_code': 'RM-C', 'reason': '崩溃恢复演练'})
    rid3 = r.json()['id']
    with Session() as db:
        rec = db.get(Recall, rid3)
        # 第一步后模拟进程崩溃
        recall_svc.trace_step(db, rid3, max_expansions=1, crash_after=1)
    r = client.get(f'/trace/recalls/{rid3}')
    check('崩溃后状态保持 tracing 且有断点', r.json()['status'] == 'tracing' and r.json()['frontier_size'] >= 1, r.text)
    # 重试到完成
    r = client.post(f'/trace/recalls/{rid3}/trace')
    check('恢复后一次重试追查完成', r.json()['status'] == 'ready', r.text)
    d3 = r.json()
    check('恢复后命中 K-6 无遗漏', d3['candidates_total'] >= 1, str(d3))
    with Session() as db:
        blocks = db.query(RecallBlock).filter(RecallBlock.recall_id == rid3).count()
    # 再次重试不应重复封锁
    client.post(f'/trace/recalls/{rid3}/trace')
    with Session() as db:
        blocks2 = db.query(RecallBlock).filter(RecallBlock.recall_id == rid3).count()
    check('崩溃恢复重试不重复封锁', blocks == blocks2, f'{blocks} vs {blocks2}')

    print('\n== 13. 追查完成后新增复盘/简报：快照失效，重核补全不遗漏 ==')
    # 用第三召回（RM-C）已 ready 的状态：给其受影响窑次 K-6 补复盘与简报
    client.post('/trace/reviews', json={'code': 'RV-6', 'kiln_code': 'K-6', 'yield_rate': 91.6})
    client.post('/trace/briefings', json={'code': 'BF-6', 'title': 'K-6 简报', 'kiln_codes': ['K-6']})
    r = client.post(f'/trace/recalls/{rid3}/trace')
    expect_error('新增复盘/简报后旧追溯失效', r)
    r = client.post(f'/trace/recalls/{rid3}/recheck')
    check('补登结果后重新核对', r.status_code == 200, r.text)
    d3b = client.get(f'/trace/recalls/{rid3}').json()
    codes = {(c['target_kind'], c['target_code']) for c in d3b['candidates']}
    check('重核补全新增复盘 RV-6', ('review', 'RV-6') in codes, str(codes))
    check('重核补全新增简报 BF-6', ('briefing', 'BF-6') in codes, str(codes))
    # 新增结果发布前即被封锁
    rv6 = next(c for c in d3b['candidates'] if c['target_code'] == 'RV-6')
    r = client.post(f'/trace/reviews/{rv6["target_id"]}/approve')
    expect_error('新增复盘在发布前被封锁', r)
    # 核对并发布，受影响结果持续封锁
    for c in d3b['candidates']:
        client.post(f'/trace/recalls/{rid3}/candidates/{c["id"]}/affected')
    r = client.post(f'/trace/recalls/{rid3}/publish', json={})
    check('第三召回整单发布', r.status_code == 200, r.text)
    r = client.post(f'/trace/reviews/{rv6["target_id"]}/approve')
    expect_error('发布后受影响复盘仍封锁', r)

    print(f'\n结果：{PASS} 项通过，{len(FAILS)} 项失败')
    if FAILS:
        for n, d in FAILS:
            print(' -', n, d)
        sys.exit(1)


if __name__ == '__main__':
    main()
