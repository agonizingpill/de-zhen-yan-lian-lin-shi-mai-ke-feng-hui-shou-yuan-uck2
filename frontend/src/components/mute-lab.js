import {el,display} from '../dom.js';
import {mixer} from '../audio-api.js';
import {desk,mutePhase,mutePhaseHint,muteStatus} from '../audio-state.js';
import {choices,field,run,write} from '../audio-form.js';
import {status} from '../widgets.js';
import {badge} from './badge.js';

const ORDER=['open','muted','reopened'];
const fmt=value=>typeof value==='number' ? value.toFixed(2) : '—';

function pointName(points,code) {
  const point=points.find(item=>item.code===code);
  return point ? code+' '+point.location : (code||'自动选择');
}

function verdictLine(trial) {
  if (trial.status==='active') return status('三段录齐后自动判定：消退 + 复现 + 对照平稳，才报源头。');
  const variant=trial.status==='source' ? 'ok' : trial.status==='void' ? 'error' : '';
  const head=trial.status==='source' ? '判定为啸叫源头'
    : trial.status==='inconclusive' ? '证据不足，不能指定源头' : '试验作废';
  return el('div',{class:'mute-verdict '+trial.status},
    el('p',{class:'status '+variant,role:'status'},el('strong',{},head),trial.reason ? '　'+trial.reason : ''));
}

function steps(trial) {
  const byPhase=Object.fromEntries((trial.segments||[]).map(seg=>[seg.phase,seg]));
  return el('div',{class:'mute-steps'},ORDER.map((phase,index)=>{
    const seg=byPhase[phase];
    return el('div',{class:'mute-step'+(seg?' recorded':' pending'),'data-phase':phase},
      el('h4',{},(index+1)+' · '+mutePhase[phase]),
      el('p',{class:'hint'},mutePhaseHint[phase]),
      el('dl',{},
        el('dt',{},'受试麦啸叫'),el('dd',{class:'mono'},seg?fmt(seg.howl_level):'待录'),
        el('dt',{},'对照麦声级'),el('dd',{class:'mono'},seg?fmt(seg.control_level):'待录')));
  }));
}

function checks(trial) {
  const c=trial.verdict_detail && trial.verdict_detail.checks;
  if (!c || !Object.keys(c).length) return null;
  const rows=[
    ['静音时啸叫消退',c.howl_suppressed_while_muted,c.howl_drop!=null?'回落 '+fmt(c.howl_drop):''],
    ['开麦后啸叫复现',c.howl_reproduced_on_reopen,c.howl_recover!=null?'回升 '+fmt(c.howl_recover):''],
    ['对照麦声级平稳',c.control_level_steady,c.control_spread!=null?'极差 '+fmt(c.control_spread):''],
  ];
  return el('ul',{class:'mute-checks'},rows.map(([label,ok,meta])=>
    el('li',{'data-ok':ok?'yes':'no'},(ok?'✓ ':'✗ ')+label,meta?el('span',{class:'mono'},'（'+meta+'）'):null)));
}

export function muteLab(role) {
  const root=el('section',{class:'mute-lab','aria-label':'短时静音试验'});
  let points=[];
  let trials=[];

  async function reload() {
    const [patch,list]=await Promise.all([mixer.patch(),mixer.muteTrials()]);
    points=patch.points||[];
    trials=list||[];
    if (!desk.mute || !trials.some(t=>t.code===desk.mute)) {
      desk.mute=(trials.find(t=>t.status==='active')||trials[0]||{}).code||null;
    }
    render();
  }

  function startForm() {
    if (!write(role)) return status('只读账号可查询三段读数与判定。');
    const live=points.filter(p=>p.patch==='live');
    return run('mute-start',[
      field('code','试验编号',{placeholder:'MT-1124-PAV',maxlength:'24'}),
      choices('point','受试麦位（选一支）',live.map(p=>[p.code,p.code+' · '+p.location+' · '+p.channel]),null),
      choices('control_point','同路对照麦（可自动）',
        [['','自动选择同一路另一支麦']].concat(live.map(p=>[p.code,p.code+' · '+p.location]))),
    ],'开试短时静音',async form=>{
      const input=Object.fromEntries(form);
      const body={code:input.code.trim(),point:input.point};
      if (input.control_point) body.control_point=input.control_point;
      const trial=await mixer.muteStart(body);
      desk.mute=trial.code;
      await reload();
      return trial.code+' 已开试，同路并发只保留这一场。';
    });
  }

  function segmentForm(trial) {
    if (!write(role)) return null;
    const count=(trial.segments||[]).length;
    if (count>=3 || trial.status!=='active') return null;
    const phase=ORDER[count];
    return run('mute-segment-'+trial.code+'-'+phase,[
      el('div',{class:'fields'},
        field('howl_level','受试麦啸叫读数（0–1）',{type:'number',min:'0',max:'1',step:'.01',required:'',placeholder:'如 0.90'}),
        field('control_level','同路对照麦声级（0–1）',{type:'number',min:'0',max:'1',step:'.01',required:'',placeholder:'如 0.30'})),
    ],'录入「'+mutePhase[phase]+'」段',async form=>{
      const howl=Number(form.get('howl_level'));
      const control=Number(form.get('control_level'));
      if (!Number.isFinite(howl)||!Number.isFinite(control)) throw new Error('请填写两段有效读数');
      await mixer.muteSegment(trial.code,{phase,howl_level:howl,control_level:control});
      await reload();
      return mutePhase[phase]+' 段已录入';
    });
  }

  function activeDeck() {
    const active=trials.filter(t=>t.status==='active');
    if (!active.length) return el('p',{class:'hint'},'当前没有进行中的静音试验。');
    return active.map(trial=>el('div',{class:'mute-active card'},
      el('h3',{class:'mono'},trial.code+' · '+pointName(points,trial.point)),
      el('p',{class:'hint'},trial.channel+' · 接线版本 v'+trial.revision+
        ' · 对照麦 '+(trial.control_point?pointName(points,trial.control_point):'未指定（按读数核对）')),
      steps(trial),
      segmentForm(trial)));
  }

  function recordDetail(trial) {
    return el('details',{class:'clip mute-record',open:trial.status==='active'?'':undefined},
      el('summary',{},
        el('code',{},trial.code),' · ',pointName(points,trial.point),' · ',trial.channel,' ',
        badge(muteStatus,trial.status)),
      steps(trial),
      verdictLine(trial),
      checks(trial),
      el('p',{class:'hint'},'开试 '+display(trial.started_by)+
        (trial.judged_by?' · 判定 '+display(trial.judged_by):'')));
  }

  function render() {
    root.replaceChildren(
      el('div',{class:'mute-head'},
        el('h2',{},'短时静音试验 · 定位啸叫发起麦'),
        el('button',{type:'button',class:'quiet',onclick:()=>reload().catch(e=>{
          root.replaceChildren(status(e.message,'error'));
        })},'刷新试验记录')),
      el('p',{class:'intro'},'几支麦同频啸叫时，选一支做开麦 → 静音 → 重新开麦三段对照；'+
        '每段记录受试麦啸叫读数与同路对照麦声级。同一路同时只留一场，判定前线路改接则试验作废。'),
      el('div',{class:'mute-grid'},
        el('div',{class:'action mute-do'},
          el('h3',{},'发起 / 录入'),
          startForm(),
          el('div',{class:'mute-active-wrap'},activeDeck())),
        el('div',{class:'mute-records'},
          el('h3',{},'三段读数与判定'),
          trials.length?trials.map(recordDetail):status('还没有静音试验，先在左侧选一支麦开试。'))));
  }

  reload().catch(error=>root.replaceChildren(status(error.message,'error')));
  return root;
}
