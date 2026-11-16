import {trace} from '../api/trace.js';
import {heading} from '../components/common/shell.js';
import {esc, notify, fmt} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {session} from '../stores/session.js';
import {TraceStatus, CandidateStatus, TargetKind} from '../constants/trace.js';

const readonly = () => session.user.role === 'viewer';
const dis = () => (readonly() ? 'disabled' : '');

const statusTone = {tracing: 'warn', ready: 'ok', rechecking: 'danger', published: 'ok'};
const candTone = {unresolved: 'warn', affected: 'danger', excluded: 'ok'};

export async function recallPage(id) {
  const [recalls, materials] = await Promise.all([trace.recalls(), trace.materials()]);
  const current = id ? await trace.recall(id).catch(() => null) : null;
  const options = materials.map(m => '<option value="' + esc(m.code) + '">' + esc(m.code + ' · ' + m.name + '</option>').join('');

  const recallCards = recalls.map(r =>
    '<a class="recall-card ' + (current && current.id === r.id ? 'active' : '') + '" href="#/recall/' + r.id + '">'
    + '<div><strong>' + esc(r.code) + '</strong>' + '<span class="trace-badge ' + (statusTone[r.status] || '') + '">' + esc(TraceStatus[r.status] || r.status) + '</span></div>'
    + '<p>快照 v' + r.graph_version + (r.stale ? ' → 关系已更新至 v' + r.current_graph_version : '（最新）') + '</p>'
    + '<small>已展开 ' + r.processed + ' / 发现 ' + r.discovered + ' · 候选 ' + r.candidates_total + '</small></a>').join('');

  let detail = '';
  if (current) {
    const c = current;
    const candRows = c.candidates.map(x =>
      '<tr class="' + (x.status === 'unresolved' ? 'row-pending' : '') + '"><td>' + esc(TargetKind[x.target_kind] || x.target_kind) + '</td>'
      + '<td>' + esc(x.target_code) + '</td>'
      + '<td><span class="trace-badge ' + (candTone[x.status] || '') + '">' + esc(CandidateStatus[x.status] || x.status) + '</span></td>'
      + '<td class="path-cell">' + x.paths.map(p => p.map(s => esc(s)).join(' → ')).join('<br>') + '</td>'
      + '<td>' + (x.exclusion_reason ? esc(x.exclusion_reason) : '—') + '</td>'
      + '<td class="action-cell">'
      + (c.status === 'published' ? (x.blocked ? '<span class="trace-badge danger">持续暂停</span>' : '<span class="trace-badge ok">已解封</span>')
        : (x.target_kind === 'pending_issue'
          ? '<button class="small aff-btn" data-cid="' + x.id + '" ' + dis() + '>确认受影响</button><br><small class="hint">待查项不可排除</small>'
          : '<button class="small aff-btn" data-cid="' + x.id + '" ' + dis() + '>受影响</button> '
            + '<button class="small exc-btn" data-cid="' + x.id + '" ' + dis() + '>排除</button>'))
      + '</td></tr>').join('');

    const unresolved = c.candidates.filter(x => x.status === 'unresolved').length;
    detail =
      '<section class="panel"><div class="section-title"><h2>' + esc(c.code) + '</h2>'
      + '<span class="trace-badge ' + (statusTone[c.status] || '') + '">' + esc(TraceStatus[c.status] || c.status) + '</span></div>'
      + '<div class="recall-meta">'
      + '<div><span>问题原料</span><strong>' + esc(c.root_batch ? c.root_batch.code : '') + ' · ' + esc(c.root_batch ? c.root_batch.name : '') + '</strong></div>'
      + '<div><span>追溯快照</span><strong>v' + c.graph_version + (c.stale ? '（已失效，当前 v' + c.current_graph_version + '）' : '（最新）') + '</strong></div>'
      + '<div><span>追查进度</span><strong>' + c.processed + ' / ' + c.discovered + '（断点 ' + c.frontier_size + '）</strong></div>'
      + '<div><span>候选核对</span><strong>' + (c.candidates_total - unresolved) + ' / ' + c.candidates_total + ' · 待核对 ' + unresolved + '</strong></div></div>'
      + (c.reason ? '<p class="hint">召回原因：' + esc(c.reason) + '</p>' : '')
      + '<div class="recall-actions">'
      + (c.status === 'tracing' ? '<button class="primary step-btn" ' + dis() + '>继续追查（断点恢复）</button><button class="step1-btn" ' + dis() + '>单步追查</button>' : '')
      + (c.status === 'rechecking' || c.stale ? '<button class="primary recheck-btn" ' + dis() + '>重新核对（保留旧结论，补全新增去向）</button>' : '')
      + (c.status === 'ready' ? '<button class="primary publish-btn" ' + dis() + '>整单发布</button>' : '')
      + (c.status === 'published' ? '<p class="warning">召回已于 ' + new Date(c.published_at * 1000).toLocaleString('zh-CN') + ' 整单发布；受影响项持续暂停批准与下载，排除项已按依据解封。</p>' : '')
      + '</div>'
      + (c.status !== 'published' && unresolved === 0 && c.status === 'ready' ? '<p class="hint">全部候选已核对，可整单发布。</p>' : '')
      + (unresolved > 0 && c.status !== 'published' ? '<p class="warning">存在 ' + unresolved + ' 项未核对（含缺来源待查领用），未核对完成前禁止发布。</p>' : '')
      + '<div class="section-title" style="margin-top:28px"><h2>受影响与排除清单（含追查路径）</h2><span>发布前列明依据</span></div>'
      + '<div class="table-wrap"><table><thead><tr><th>类型</th><th>对象</th><th>结论</th><th>沿配料链追查路径</th><th>排除依据</th><th>操作</th></tr></thead><tbody>'
      + (candRows || '<tr><td colspan="6" class="empty">暂无候选，先继续追查。</td></tr>') + '</tbody></table></div></section>';
  } else {
    detail = '<section class="panel"><div class="empty">选择左侧召回单查看追查路径与核对清单，或先按原料批号发起召回。</div></section>';
  }

  return heading('07 / RECALL', '原料召回与配料链追查',
    '按原料批号发起召回，沿多级混配与领用链追查受影响窑次、复盘、简报；缺来源领用暂列待查。中断可恢复，关系更新使旧追溯失效，重新核对后整单发布。')
    + '<div class="recall-grid"><section class="panel"><div class="section-title"><h2>发起召回</h2><span>按原料批号</span></div>'
    + '<form id="f-recall" class="trace-form">'
    + '<label>问题原料批号<select name="batch_code">' + options + '</select></label>'
    + '<label>召回原因<input name="reason" placeholder="含铁量异常 / 供应商通报…"></label>'
    + '<button class="primary" ' + dis() + '>发起召回并立即追查</button></form>'
    + '<div class="section-title" style="margin-top:32px"><h2>召回单</h2><span>' + recalls.length + ' 单</span></div>'
    + '<div class="recall-list">' + (recallCards || '<p class="empty">暂无召回单</p>') + '</div></section>'
    + '<div class="recall-detail-col">' + detail + '</div></div>';
}

export function bindRecall(refresh, id) {
  const form = document.querySelector('#f-recall');
  if (form) submit(form, async f => {
    const r = await trace.createRecall({batch_code: f.get('batch_code'), reason: f.get('reason')});
    notify('召回已发起，开始沿配料链追查');
    await trace.trace(r.id);
    location.hash = '#/recall/' + r.id;
  });
  if (!id) return;
  const rid = Number(id);
  const reload = async msg => { if (msg) notify(msg); await refresh(); };

  document.querySelectorAll('.step-btn').forEach(b => b.onclick = async () => {
    try { await trace.trace(rid); await reload('追查已从断点继续'); } catch (e) { notify(e.message, true); }
  });
  document.querySelectorAll('.step1-btn').forEach(b => b.onclick = async () => {
    try { await trace.trace(rid, 1); await reload('已单步展开一个批号'); } catch (e) { notify(e.message, true); }
  });
  document.querySelectorAll('.recheck-btn').forEach(b => b.onclick = async () => {
    try { await trace.recheck(rid); await reload('已按最新关系重新核对，新增去向已补全'); } catch (e) { notify(e.message, true); }
  });
  document.querySelectorAll('.publish-btn').forEach(b => b.onclick = async () => {
    const note = prompt('整单发布备注（可留空）：', '') ?? '';
    try { await trace.publish(rid, note); await reload('召回已整单发布，门禁按受影响/排除结论处置'); } catch (e) { notify(e.message, true); }
  });
  document.querySelectorAll('.aff-btn').forEach(b => b.onclick = async () => {
    try { await trace.markAffected(rid, Number(b.dataset.cid)); await reload('已标记受影响，发布前持续暂停'); } catch (e) { notify(e.message, true); }
  });
  document.querySelectorAll('.exc-btn').forEach(b => b.onclick = async () => {
    const reason = prompt('请填写排除依据（必填）：', '');
    if (reason === null) return;
    try { await trace.exclude(rid, Number(b.dataset.cid), reason); await reload('已排除并记录依据，发布后解封'); } catch (e) { notify(e.message, true); }
  });
}
