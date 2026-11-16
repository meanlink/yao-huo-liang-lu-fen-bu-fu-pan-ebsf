import {trace} from '../api/trace.js';
import {heading} from '../components/common/shell.js';
import {esc, notify, fmt} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {session} from '../stores/session.js';

const readonly = () => session.user.role === 'viewer';
const dis = () => (readonly() ? 'disabled' : '');

function badge(text, tone = '') {
  return '<span class="trace-badge ' + tone + '">' + esc(text) + '</span>';
}

export async function registerPage() {
  const [materials, kilns, issues, reviews, briefings, mixes] = await Promise.all([
    trace.materials(), trace.kilns(), trace.issues(), trace.reviews(), trace.briefings(), trace.mixes(),
  ]);
  const materialOptions = materials.map(m => '<option value="' + esc(m.code) + '">' + esc(m.code + ' · ' + m.name) + '</option>').join('');
  const kilnOptions = kilns.map(k => '<option value="' + esc(k.code) + '">' + esc(k.code) + '</option>').join('');

  const materialRows = materials.map(m =>
    '<tr><td><button class="text-button gene-btn" data-code="' + esc(m.code) + '">' + esc(m.code) + '</button></td>' +
    '<td>' + esc(m.name) + '</td><td>' + (m.kind === 'raw' ? '原料' : '混配') + '</td>' +
    '<td>' + esc(m.supplier || '—') + '</td><td>' + fmt(m.initial_qty) + '</td>' +
    '<td><strong>' + fmt(m.on_hand_qty) + '</strong></td></tr>').join('');

  const issueRows = issues.map(i =>
    '<tr' + (i.status === 'pending' ? ' class="row-pending"' : '') + '><td>' + esc(i.code) + '</td>' +
    '<td>' + (i.batch_id ? esc(i.material_name) : badge('缺来源·待查', 'warn')) + '</td>' +
    '<td>' + fmt(i.qty) + '</td><td>' + esc(i.kiln_code) + '</td><td>' +
    (i.status === 'pending'
      ? (i.blocked ? badge('召回暂停中', 'danger') + '<br><small>发布后补登</small>'
         : '<select class="resolve-sel" data-id="' + i.id + '"><option value="">补登来源批号…</option>' + materialOptions + '</select>')
      : badge('已关联', 'ok')) + '</td></tr>').join('');

  const reviewRows = reviews.map(r =>
    '<tr><td>' + esc(r.code) + '</td><td>' + esc(r.kiln_code) + '</td><td>' + fmt(r.yield_rate) + '%</td>' +
    '<td>' + esc(r.note || '—') + '</td><td>' +
    (r.blocked ? badge('暂停批准', 'danger') + '<br><small>' + esc(r.block_reason) + '</small>'
      : '<button class="small approve-btn" data-id="' + r.id + '" ' + dis() + '>批准</button>') + '</td></tr>').join('');

  const briefingRows = briefings.map(b =>
    '<tr><td>' + esc(b.code) + '</td><td>' + esc(b.title) + '</td><td>' + esc(b.kiln_codes.join('、')) + '</td><td>' +
    (b.blocked ? badge('暂停下载', 'danger')
      : '<button class="small dl-btn" data-id="' + b.id + '" data-code="' + esc(b.code) + '">下载</button>') + '</td></tr>').join('');

  const mixRows = mixes.map(o =>
    '<tr><td>' + esc(o.code) + '</td><td>' +
    '投入 ' + o.inputs.map(x => esc(x.batch_code) + '×' + fmt(x.qty)).join('，') + '<br>' +
    '产出 ' + o.outputs.map(x => esc(x.batch_code) + '×' + fmt(x.qty)).join('，') +
    (o.loss_qty ? '<br><small>损耗 ' + fmt(o.loss_qty) + '</small>' : '') + '</td>' +
    '<td>v' + o.graph_version + '</td></tr>').join('');

  return heading('06 / GLAZE TRACE', '釉料用料登记',
    '登记原料批号、多级混配拆合与窑次领用；投料与余量全程守恒，循环与超领直接拒绝。缺来源领用暂列待查，不能当作无影响。')
    + '<div class="trace-grid">'
    // 左列：登记表单
    + '<section class="panel"><div class="section-title"><h2>登记原料批号</h2><span>入库即建立守恒余量</span></div>'
    + '<form id="f-raw" class="trace-form">'
    + '<label>批号编码<input name="code" required placeholder="RM-20261116-01"></label>'
    + '<label>物料名称<input name="name" required placeholder="长石粉"></label>'
    + '<label>供应商（可选）<input name="supplier" placeholder="甲矿"></label>'
    + '<label>登记数量<input name="qty" type="number" step="0.001" min="0.001" required></label>'
    + '<button class="primary" ' + dis() + '>登记原料</button></form>'

    + '<div class="section-title" style="margin-top:34px"><h2>登记混配（多级拆合）</h2><span>投入 = 产出 + 损耗</span></div>'
    + '<form id="f-mix" class="trace-form">'
    + '<label>混配单号<input name="code" required placeholder="MIX-001"></label>'
    + '<label>投入行（批号:数量，分号分隔）<input name="inputs" required placeholder="RM-A:40;RM-B:20"></label>'
    + '<label>产出行（批号:数量，分号分隔；新批号自动建账）<input name="outputs" required placeholder="GLAZE-1:55"></label>'
    + '<label>损耗数量<input name="loss_qty" type="number" step="0.001" min="0" value="0"></label>'
    + '<p class="hint">成环混配、余量不足、总量不守恒都会被服务端拒绝。</p>'
    + '<button class="primary" ' + dis() + '>提交混配</button></form>'

    + '<div class="section-title" style="margin-top:34px"><h2>登记窑次</h2></div>'
    + '<form id="f-kiln" class="trace-form">'
    + '<div class="parameter-grid"><label>窑次号<input name="code" required placeholder="QH-001"></label>'
    + '<label>窑线<input name="line" placeholder="L1"></label>'
    + '<label>烧成件数<input name="fired" type="number" min="0" required></label>'
    + '<label>合格件数<input name="passed" type="number" min="0" required></label></div>'
    + '<button class="primary" ' + dis() + '>登记窑次</button></form></section>'

    // 中列：领用与结果登记
    + '<section class="panel"><div class="section-title"><h2>登记领用</h2><span>并发超领拒绝</span></div>'
    + '<form id="f-issue" class="trace-form">'
    + '<label>领用单号<input name="code" required placeholder="IS-001"></label>'
    + '<label>来源批号<select name="batch_code">' + materialOptions + '</select></label>'
    + '<div class="parameter-grid"><label>数量<input name="qty" type="number" step="0.001" min="0.001" required></label>'
    + '<label>窑次<select name="kiln_code">' + kilnOptions + '</select></label></div>'
    + '<button class="primary" ' + dis() + '>登记领用</button></form>'

    + '<div class="section-title" style="margin-top:34px"><h2>缺来源领用（暂列待查）</h2><span>不得当作无影响</span></div>'
    + '<form id="f-pending" class="trace-form">'
    + '<label>领用单号<input name="code" required placeholder="IS-P-001"></label>'
    + '<label>物料名称（用于召回时按同名命中）<input name="material_name" required></label>'
    + '<div class="parameter-grid"><label>数量<input name="qty" type="number" step="0.001" min="0.001" required></label>'
    + '<label>窑次<select name="kiln_code">' + kilnOptions + '</select></label></div>'
    + '<button class="primary" ' + dis() + '>登记待查</button></form>'

    + '<div class="section-title" style="margin-top:34px"><h2>复盘 / 简报登记</h2></div>'
    + '<form id="f-review" class="trace-form">'
    + '<div class="parameter-grid"><label>复盘号<input name="code" required placeholder="RV-001"></label>'
    + '<label>窑次<select name="kiln_code">' + kilnOptions + '</select></label></div>'
    + '<label>良率 %<input name="yield_rate" type="number" step="0.01" min="0" max="100" required></label>'
    + '<label>备注<input name="note"></label>'
    + '<button class="primary" ' + dis() + '>登记复盘</button></form>'
    + '<form id="f-briefing" class="trace-form" style="margin-top:22px">'
    + '<label>简报号<input name="code" required placeholder="BF-001"></label>'
    + '<label>标题<input name="title" required></label>'
    + '<label>关联窑次（逗号分隔）<input name="kiln_codes" required placeholder="QH-001,QH-002"></label>'
    + '<label>内容<textarea name="content" rows="3"></textarea></label>'
    + '<button class="primary" ' + dis() + '>登记简报</button></form></section>'

    // 右列：台账
    + '<section class="panel"><div class="section-title"><h2>物料台账与余量</h2><span>' + materials.length + ' 个批号</span></div>'
    + '<div class="table-wrap"><table><thead><tr><th>批号</th><th>名称</th><th>类型</th><th>供应商</th><th>初始</th><th>余量</th></tr></thead><tbody>'
    + (materialRows || '<tr><td colspan="6" class="empty">暂无批号</td></tr>') + '</tbody></table></div>'
    + '<div id="gene-box" class="gene-box" hidden></div>'

    + '<div class="section-title" style="margin-top:30px"><h2>领用清单</h2><span>待查 ' + issues.filter(i => i.status === 'pending').length + ' 笔</span></div>'
    + '<div class="table-wrap"><table><thead><tr><th>单号</th><th>物料</th><th>数量</th><th>窑次</th><th>状态</th></tr></thead><tbody>'
    + (issueRows || '<tr><td colspan="5" class="empty">暂无领用</td></tr>') + '</tbody></table></div>'

    + '<div class="section-title" style="margin-top:30px"><h2>混配记录</h2><span>' + mixes.length + ' 单</span></div>'
    + '<div class="table-wrap"><table><thead><tr><th>单号</th><th>守恒明细</th><th>版本</th></tr></thead><tbody>'
    + (mixRows || '<tr><td colspan="3" class="empty">暂无混配</td></tr>') + '</tbody></table></div>'

    + '<div class="section-title" style="margin-top:30px"><h2>复盘与批准门禁</h2></div>'
    + '<div class="table-wrap"><table><thead><tr><th>复盘</th><th>窑次</th><th>良率</th><th>备注</th><th>操作</th></tr></thead><tbody>'
    + (reviewRows || '<tr><td colspan="5" class="empty">暂无复盘</td></tr>') + '</tbody></table></div>'

    + '<div class="section-title" style="margin-top:30px"><h2>简报与下载门禁</h2></div>'
    + '<div class="table-wrap"><table><thead><tr><th>简报</th><th>标题</th><th>窑次</th><th>操作</th></tr></thead><tbody>'
    + (briefingRows || '<tr><td colspan="4" class="empty">暂无简报</td></tr>') + '</tbody></table></div></section>'
    + '</div>';
}

function parseLines(text) {
  return text.split(';').map(s => s.trim()).filter(Boolean).map(pair => {
    const [code, qty] = pair.split(':').map(s => s.trim());
    return {code, qty: Number(qty)};
  });
}

export function bindRegister(refresh) {
  const reload = async msg => { notify(msg); await refresh(); };
  submit(document.querySelector('#f-raw'), async f => {
    await trace.registerRaw({code: f.get('code'), name: f.get('name'), supplier: f.get('supplier'), qty: Number(f.get('qty'))});
    await reload('原料已登记，余量台账已建立');
  });
  submit(document.querySelector('#f-mix'), async f => {
    const inputs = parseLines(f.get('inputs')), outputs = parseLines(f.get('outputs'));
    if (inputs.some(x => !x.code || !(x.qty > 0)) || outputs.some(x => !x.code || !(x.qty > 0))) throw new Error('投入/产出行格式应为 批号:数量，分号分隔');
    await trace.createMix({code: f.get('code'), loss_qty: Number(f.get('loss_qty') || 0), inputs, outputs});
    await reload('混配已登记，投入与余量守恒通过');
  });
  submit(document.querySelector('#f-kiln'), async f => {
    await trace.registerKiln({code: f.get('code'), line: f.get('line'), fired: Number(f.get('fired')), passed: Number(f.get('passed')), fired_at: Date.now() / 1000});
    await reload('窑次已登记');
  });
  submit(document.querySelector('#f-issue'), async f => {
    await trace.createIssue({code: f.get('code'), batch_code: f.get('batch_code'), qty: Number(f.get('qty')), kiln_code: f.get('kiln_code')});
    await reload('领用已登记，余量已扣减');
  });
  submit(document.querySelector('#f-pending'), async f => {
    await trace.createPending({code: f.get('code'), material_name: f.get('material_name'), qty: Number(f.get('qty')), kiln_code: f.get('kiln_code')});
    await reload('缺来源领用已暂列待查，召回时将按同名物料命中');
  });
  submit(document.querySelector('#f-review'), async f => {
    await trace.registerReview({code: f.get('code'), kiln_code: f.get('kiln_code'), yield_rate: Number(f.get('yield_rate')), note: f.get('note')});
    await reload('复盘已登记');
  });
  submit(document.querySelector('#f-briefing'), async f => {
    const kiln_codes = f.get('kiln_codes').split(',').map(s => s.trim()).filter(Boolean);
    await trace.registerBriefing({code: f.get('code'), title: f.get('title'), content: f.get('content'), kiln_codes});
    await reload('简报已登记');
  });
  document.querySelectorAll('.approve-btn').forEach(btn => btn.onclick = async () => {
    try { await trace.approveReview(btn.dataset.id); notify('复盘已批准'); await refresh(); }
    catch (e) { notify(e.message, true); }
  });
  document.querySelectorAll('.resolve-sel').forEach(sel => sel.onchange = async () => {
    if (!sel.value) return;
    try { await trace.resolveIssue(Number(sel.dataset.id), sel.value); notify('待查领用已补登来源并扣减余量'); await refresh(); }
    catch (e) { notify(e.message, true); sel.value = ''; }
  });
  document.querySelectorAll('.gene-btn').forEach(btn => btn.onclick = async () => showGenealogy(btn.dataset.code));
  document.querySelectorAll('.dl-btn').forEach(btn => btn.onclick = async () => {
    try {
      const r = await fetch(trace.downloadBriefing(btn.dataset.id), {headers: {Authorization: 'Bearer ' + session.token}});
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || '下载失败');
      }
      const u = URL.createObjectURL(await r.blob());
      const a = document.createElement('a');
      a.href = u; a.download = '简报-' + btn.dataset.code + '.txt'; a.click();
      setTimeout(() => URL.revokeObjectURL(u), 1000);
    } catch (e) { notify(e.message, true); }
  });
}

async function showGenealogy(code) {
  const box = document.querySelector('#gene-box');
  box.hidden = false;
  box.innerHTML = '<p class="hint">正在加载 ' + esc(code) + ' 的配料链…</p>';
  try {
    const [g, ledger] = await Promise.all([trace.genealogy(code), trace.ledger(code)]);
    const mixNode = n => '<div class="gene-node"><strong>' + esc(n.mix_code) + '</strong>'
      + '<p>投入：' + n.inputs.map(x => esc(x.code) + '×' + fmt(x.qty)).join('，') + '</p>'
      + '<p>产出：' + n.outputs.map(x => esc(x.code) + '×' + fmt(x.qty)).join('，')
      + (n.loss_qty ? '（损耗 ' + fmt(n.loss_qty) + '）' : '') + '</p></div>';
    box.innerHTML = '<h3>批号 ' + esc(g.batch.code) + ' 的配料链与守恒流水</h3>'
      + (g.produced_by.length ? '<p class="hint">由以下混配产出：</p>' + g.produced_by.map(mixNode).join('') : '<p class="hint">原料批号，无上游混配。</p>')
      + (g.consumed_in.length ? '<p class="hint">投入以下混配：</p>' + g.consumed_in.map(mixNode).join('') : '')
      + (g.issues.length ? '<p class="hint">领用：' + g.issues.map(i => esc(i.code) + '→' + esc(i.kiln_code) + '×' + fmt(i.qty)).join('；') + '</p>' : '')
      + '<table><thead><tr><th>变动</th><th>结余</th><th>事由</th><th>单据</th></tr></thead><tbody>'
      + ledger.map(l => '<tr' + (l.change_qty < 0 ? ' class="row-pending"' : '') + '><td>' + fmt(l.change_qty) + '</td><td>' + fmt(l.balance_after) + '</td><td>' + esc(l.reason) + '</td><td>' + esc(l.ref_code) + '</td></tr>').join('')
      + '</tbody></table>';
    box.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  } catch (e) { box.innerHTML = '<p class="warning">' + esc(e.message) + '</p>'; }
}
