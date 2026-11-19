import {BrowserAdapter, LocalAdapter} from './adapters.mjs';
import {escapeHTML as e, renderGuide} from './lesson-view.mjs';
import {simulateSchedule, simulateCache, simulateHash, createNetworkScenario, probeNetwork, networkHints} from './engines.mjs';

const $=selector=>document.querySelector(selector);
const $$=selector=>[...document.querySelectorAll(selector)];
const copy=value=>structuredClone(value);
const LABS={
  network:{subject:'network',title:'网络故障排查',description:'看懂拓扑，选择诊断方法，找到证据后修复配置。',
    question:'为什么这台电脑打不开网站？',model:'固定链路的简化 IPv4 网络；检查请求和回复路径及 DNS 服务。不模拟真实设备、丢包、NAT、TCP 握手或物理传输时延。',
    hints:['先分开检查本地网关、服务器 IP 和域名解析。']},
  scheduler:{subject:'os',title:'进程调度实验',description:'改变到达时间与时间片，比较执行顺序和等待代价。',question:'小时间片会让谁先完成？',
    model:'单 CPU、整数时间、无 I/O 阻塞，忽略切换开销。同刻到达按输入顺序；片末到达任务先入队，再将未完成任务回队。',
    hints:['先画出任务到达时刻，只有已到达的任务能进入就绪队列。','轮转每次运行 min(时间片, 剩余时间)；未完成才回队。','周转=完成−到达；本模型等待=周转−运行时长。对照时间线逐项核算。']},
  cache:{subject:'organization',title:'直接映射 Cache',description:'逐次拆分地址，观察命中与不同内存块的相互覆盖。',question:'访问过的地址为什么还会缺失？',
    model:'16 位字节地址、初始为空、只读直接映射。行数与块大小为 2 的幂；不包含写策略、多级 Cache 或一致性。',
    hints:['先按块大小把地址分组，相邻地址可能位于同一块。','块号=⌊地址/块大小⌋；索引=块号 mod 行数；标记=⌊块号/行数⌋。','有效位为真且标记相同才命中；同一行的不同块会互相覆盖。']},
  hash:{subject:'data-structures',title:'散列与线性探测',description:'一步一步处理冲突，观察探测次数、回绕和表满终止。',question:'一次查找真的只看一个槽吗？',
    model:'非负整数键，h(k)=k mod m，线性探测；仅含插入与查找。初始空表，重复插入不新增，最多检查 m 个槽。',
    hints:['先计算 key mod m，确认最初访问的位置。','有冲突就查下一槽，末尾按模运算回到开头。','遇到同键即可找到；本模型无删除，查找遇空可终止，满表最多查 m 槽。']},
};
let bundle,adapter,lessonMap=new Map(),progress={},currentLesson=null,currentPath=null,view='home';
let localMode=false,notesDirty=false,notesSaving=null,navigationEpoch=0,lab=null,player=null,toastTimer;

function message(selector,text){const element=$(selector);element.textContent=text;element.hidden=!text;}
function toast(text,error=false){clearTimeout(toastTimer);$('#toast').textContent=text;$('#toast').classList.toggle('error',error);$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,5000);}
function storageWarning(text){message('#storage-warning',text);$('#storage-mode').textContent='临时学习模式 · 关闭前请导出记录';}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function jsonDownload(value,name){download(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}),name);}
function stopPlayer(){clearInterval(player);player=null;$('#auto-play').textContent='自动播放';}
function showScreen(name){stopPlayer();view=name;$$('.screen').forEach(s=>s.hidden=s.id!==name+'-screen');$('#main').focus({preventScroll:true});window.scrollTo({top:0});}
function setRoute(hash){history.replaceState(null,'','#'+hash);}
function selectNav(id){$$('#subject-nav button').forEach(b=>{if(b.dataset.subject===id)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});}
function subjectOf(path){return bundle.subjects.find(s=>s.id===path.subject_id);}
function stat(id){const r=progress[id];return {started:!!r,completed:!!r?.completed,
  attempted:Array.isArray(r?.attempts)?r.attempts.length:r?.attempted||0,
  correct:Array.isArray(r?.attempts)?r.attempts.filter(a=>a.correct).length:r?.correct||0};}
async function refreshProgress(){progress=await adapter.summaries();}

function renderHome(){
  selectNav(null);
  $('#course-facts').innerHTML=`<span>4 门核心课</span><span>${lessonMap.size} 节课时（含微课堂）</span><span>4 类交互实验</span>`;
  const symbols={network:'NETWORK',os:'OPERATING SYSTEMS',organization:'ORGANIZATION','data-structures':'DATA STRUCTURES'};
  $('#subject-cards').innerHTML=bundle.subjects.map(s=>{
    const paths=bundle.courses.filter(p=>p.subject_id===s.id),lessons=paths.flatMap(p=>p.lessons),done=lessons.filter(l=>stat(l.id).completed).length;
    return `<article class="subject-card"><span class="subject-symbol">${symbols[s.id]}</span><h3>${e(s.title)}</h3><p>${e(s.description)}</p><span class="card-count">${lessons.length} 节课时 · ${lessons.reduce((n,l)=>n+l.guide.quiz.length,0)} 道自测<br>已标记学完 ${done} 节</span><button class="button" data-subject="${e(s.id)}">进入课程</button></article>`;
  }).join('');
  $('#lab-cards').innerHTML=Object.entries(LABS).map(([id,l])=>`<article class="lab-card"><span class="lab-badge">${e(bundle.subjects.find(s=>s.id===l.subject).title)}</span><h3>${e(l.question)}</h3><p>${e(l.description)}</p><button class="button quiet small" data-lab="${id}">开始${e(l.title)}</button></article>`).join('');
}
async function goHome(){if(!await flushNotes())return;++navigationEpoch;showScreen('home');setRoute('home');renderHome();}
async function goSubject(id){const path=bundle.courses.find(c=>c.subject_id===id);if(path)await openLesson(path.lessons[0].id);}

function renderOutline(){
  const subject=subjectOf(currentPath);
  selectNav(subject.id);
  $('#outline-subject').textContent=subject.title;
  $('#path-select').innerHTML=bundle.courses.filter(c=>c.subject_id===subject.id).map(c=>`<option value="${e(c.id)}" ${c.id===currentPath.id?'selected':''}>${e(c.title)}</option>`).join('');
  const search=$('#lesson-search').value.trim().toLocaleLowerCase();
  $('#lesson-list').innerHTML=currentPath.lessons.filter(l=>!search||[l.title,...l.guide.knowledge_points.map(p=>p.title)].join(' ').toLocaleLowerCase().includes(search)).map(l=>{
    const r=stat(l.id);
    return `<button class="lesson-link" data-lesson="${e(l.id)}" ${currentLesson?.id===l.id?'aria-current="page"':''}><small>${String(l.number).padStart(2,'0')}</small><span>${e(l.title)}</span>${r.completed?'<span class="lesson-done" aria-label="已标记学完">✓</span>':''}</button>`;
  }).join('')||'<p class="muted">没有匹配课时，请换个关键词。</p>';
  $('#path-progress').textContent=`${currentPath.lessons.filter(l=>stat(l.id).completed).length} / ${currentPath.lessons.length} 节已标记学完`;
  $('#outline-lab').dataset.lab=currentPath.lab_id;
}
async function openLesson(id,{focusQuiz=false}={}){
  if(!lessonMap.has(id)||!await flushNotes())return;
  const epoch=++navigationEpoch,info=lessonMap.get(id);
  message('#lesson-error','');$('#lesson-loading').hidden=false;
  try{
    const opened=await adapter.open(id);
    if(epoch!==navigationEpoch)return;
    currentLesson=opened;currentPath=info.course;notesDirty=false;
    await refreshProgress();
    if(epoch!==navigationEpoch)return;
    $('#lesson-search').value='';showScreen('study');selectNav(currentPath.subject_id);setRoute('lesson/'+encodeURIComponent(id));
    renderOutline();
    $('#reading-course').textContent=currentPath.title;
    $('#reading-position').textContent=`${info.lesson.number} / ${currentPath.lessons.length}`;
    $('#lesson-title').textContent=opened.title;
    $('#lesson-content').innerHTML=renderGuide(opened);
    opened.attempts?.forEach(a=>{
      const radio=$(`#lesson-content input[name="q${a.question_index}"][value="${a.choice}"]`);
      if(radio){radio.checked=true;const q=opened.guide.quiz[a.question_index];showQuizResult(a.question_index,{correct:!!a.correct,answer:q.answer,explanation:q.explanation});}
    });
    const index=currentPath.lessons.findIndex(l=>l.id===id);
    $('#previous-lesson').disabled=index===0;$('#next-lesson').disabled=index===currentPath.lessons.length-1;
    $('#complete-lesson').textContent=opened.completed?'已标记学完 · 撤销标记':'标记学完';
    if(focusQuiz)$('#quiz').scrollIntoView({block:'start'});
    else $('#lesson-title').scrollIntoView({block:'start'});
  }catch(error){message('#lesson-error',error.message);toast(error.message,true);}
  finally{if(epoch===navigationEpoch)$('#lesson-loading').hidden=true;}
}
function showQuizResult(index,result){const out=$('#feedback-'+index);if(!out)return;out.textContent=`${result.correct?'回答正确':'再想一想'} · 答案 ${String.fromCharCode(65+result.answer)}。${result.explanation}`;out.classList.toggle('correct',result.correct);out.classList.toggle('incorrect',!result.correct);}
async function saveNotes(){
  if(notesSaving)return notesSaving;
  if(!currentLesson||!notesDirty)return true;
  const id=currentLesson.id,text=$('#lesson-notes').value,revision=currentLesson.notes_revision;
  notesSaving=(async()=>{
    try{
      const result=await adapter.saveNotes(id,text,revision);
      if(currentLesson?.id===id){currentLesson.notes_revision=result.revision;currentLesson.notes=text;notesDirty=$('#lesson-notes').value!==text;$('#notes-state').textContent=notesDirty?'还有未保存修改':'已保存';message('#notes-error','');$('#reload-notes').hidden=true;}
      return true;
    }catch(error){message('#notes-error',error.message);$('#notes-state').textContent='草稿尚未保存';$('#reload-notes').hidden=false;return false;}
    finally{notesSaving=null;}
  })();
  return notesSaving;
}
async function flushNotes(){
  if(!notesDirty)return true;
  if(!await saveNotes()){toast('当前笔记尚未保存，请先处理草稿，再切换页面。',true);return false;}
  return notesDirty?flushNotes():true;
}

function parseIntegers(text){const parts=text.trim().split(/[\s,，]+/).filter(Boolean);if(parts.some(p=>! /^(?:0x[0-9a-f]+|\d+)$/i.test(p)))throw new Error('请输入用空格或逗号分隔的十进制或 0x 十六进制整数。');return parts.map(Number);}
function formInputs(id,seed){
  if(id==='network')return `<label for="network-task">实验任务</label><select id="network-task"><option value="mask">子网边界</option><option value="gateway">默认网关</option><option value="dns">域名与 IP</option><option value="route">路由转发</option><option value="transfer">迁移挑战</option></select><label for="experiment-seed">变式种子</label><input id="experiment-seed" type="number" min="1" max="1000000" value="${seed}" required>`;
  if(id==='scheduler')return `<label for="schedule-algorithm">调度算法</label><select id="schedule-algorithm"><option value="rr">时间片轮转 RR</option><option value="fcfs">先来先服务 FCFS</option></select><label for="schedule-quantum">时间片</label><input id="schedule-quantum" type="number" min="1" max="100" value="2" required><label for="schedule-jobs">进程：名称，到达时刻，运行时长</label><textarea id="schedule-jobs" rows="4" required>A,0,5\nB,0,3</textarea>`;
  if(id==='cache')return `<div class="inline-fields"><div><label for="cache-lines">Cache 行数</label><select id="cache-lines"><option>1</option><option>2</option><option selected>4</option><option>8</option><option>16</option></select></div><div><label for="cache-block">块大小 / Byte</label><select id="cache-block"><option>1</option><option>2</option><option selected>4</option><option>8</option><option>16</option></select></div></div><label for="cache-addresses">访问地址（逗号分隔）</label><textarea id="cache-addresses" rows="3" required>0,1,16,0</textarea>`;
  return `<label for="hash-size">槽位数 m</label><select id="hash-size"><option selected>7</option><option>11</option><option>17</option></select><label for="hash-operations">操作：+键 插入，?键 查找</label><textarea id="hash-operations" rows="4" required>+10,+17,+24,?17,?31</textarea><p class="muted small-text">键为 0～9999 的整数，使用逗号分隔。</p>`;
}
async function openLab(id){
  if(!LABS[id]||!await flushNotes())return;
  ++navigationEpoch;
  stopPlayer();lab={id,seed:1,result:null,cursor:0,session:null,hint:0,scenario:null,config:null};
  showScreen('lab');setRoute('lab/'+id);selectNav(LABS[id].subject);
  const meta=LABS[id];$('#lab-subject').textContent=bundle.subjects.find(s=>s.id===meta.subject).title;
  $('#lab-title').textContent=meta.title;$('#lab-description').textContent=meta.description;$('#lab-model').textContent=meta.model;
  $('#experiment-inputs').innerHTML=formInputs(id,1);$('#prediction').value='';$('#prediction').readOnly=false;
  invalidateExperiment();
  const sessions=await adapter.sessions();
  if(view==='lab'&&lab?.id===id){lab.savedSession=[...sessions].reverse().find(s=>s.labId===id&&s.version===1&&s.engineVersion===1);$('#resume-experiment').hidden=!lab.savedSession;}
}
function invalidateExperiment(){
  stopPlayer();if(!lab)return;lab.result=null;lab.cursor=0;lab.session=null;lab.hint=0;lab.busy=false;
  $('#playback-controls').removeAttribute('aria-busy');
  $('#prediction').readOnly=false;$('#experiment-state').textContent='等待你的预测';$('#playback-controls').hidden=true;$('#experiment-review').hidden=true;
  $('#resume-experiment').hidden=true;
  $('#hint-text').textContent='先观察现象，再决定检查哪一项。';$('#next-hint').disabled=false;
  $('#operation-list').innerHTML='';$('#log-count').textContent='0';$('#lab-explanation').innerHTML='';message('#experiment-error','');
  $('#network-config').hidden=lab.id!=='network';
  if(lab.id==='network'){
    try{lab.seed=Number($('#experiment-seed').value);lab.scenario=createNetworkScenario($('#network-task').value,lab.seed);lab.config=copy(lab.scenario.config);renderNetworkConfig(false);$('#lab-visual').innerHTML=networkSVG();}
    catch(error){message('#experiment-error',error.message);$('#lab-visual').innerHTML='<p class="empty-state">请填写有效的任务参数。</p>';}
  }else $('#lab-visual').innerHTML='<p class="empty-state">先填写预测，开始后单步观察状态变化。</p>';
  $('#variant-label').textContent=`变式种子 ${lab.seed} · 模型版本 1`;
}
function readInputs(){
  if(lab.id==='network')return {kind:$('#network-task').value,seed:Number($('#experiment-seed').value)};
  if(lab.id==='scheduler'){
    const rows=$('#schedule-jobs').value.trim().split('\n').filter(s=>s.trim());
    const processes=rows.map(row=>{const parts=row.split(/[,，]/).map(s=>s.trim());if(parts.length!==3||!/^\d+$/.test(parts[1])||!/^\d+$/.test(parts[2]))throw new Error('每行请填写：名称,非负到达时刻,正运行时长。');return {id:parts[0],arrival:Number(parts[1]),burst:Number(parts[2])};});
    return {processes,algorithm:$('#schedule-algorithm').value,quantum:Number($('#schedule-quantum').value)};
  }
  if(lab.id==='cache')return {addresses:parseIntegers($('#cache-addresses').value),lines:Number($('#cache-lines').value),blockSize:Number($('#cache-block').value)};
  const operations=$('#hash-operations').value.trim().split(/[\s,，]+/).filter(Boolean).map(s=>{const m=s.match(/^([+?])(\d+)$/);if(!m)throw new Error('操作请用 +10 表示插入，?10 表示查找。');return {kind:m[1]==='+'?'insert':'search',key:Number(m[2])};});
  return {operations,size:Number($('#hash-size').value)};
}
async function startExperiment(){
  try{
    const prediction=$('#prediction').value.trim();if(!prediction)throw new Error('先写下你的预测与理由。');
    const input=readInputs();stopPlayer();
    if(lab.id==='network'){lab.scenario=createNetworkScenario(input.kind,input.seed);lab.config=copy(lab.scenario.config);lab.result=null;lab.seed=input.seed;}
    else if(lab.id==='scheduler')lab.result=simulateSchedule(input.processes,input);
    else if(lab.id==='cache')lab.result=simulateCache(input.addresses,input);
    else lab.result=simulateHash(input.operations,input);
    lab.cursor=0;lab.hint=0;lab.session={version:1,engineVersion:1,id:crypto.randomUUID(),labId:lab.id,seed:lab.seed,
      createdAt:new Date().toISOString(),input:copy(input),prediction,reflection:'',hintCount:0,history:[],discardedEvents:0,status:'started',maxStep:0};
    $('#reflection').value='';$('#prediction').readOnly=true;$('#experiment-state').textContent='实验已开始';message('#experiment-error','');
    $('#resume-experiment').hidden=true;
    $('#experiment-review').hidden=true;$('#hint-text').textContent='先观察现象，再决定检查哪一项。';$('#next-hint').disabled=false;
    await record('start','记录预测，建立初始状态。');
    if(lab.id==='network'){renderNetworkConfig(true);$('#lab-visual').innerHTML=networkSVG();$('#lab-explanation').innerHTML='<p>先选择一项诊断。修改配置后，请再次测试网站并检查回复路径。</p>';$('#playback-controls').hidden=true;}
    else{$('#playback-controls').hidden=false;renderFrame();}
  }catch(error){message('#experiment-error',error.message);}
}
async function record(kind,details){
  if(!lab?.session)return;
  const session=lab.session;session.history.push({at:new Date().toISOString(),kind,details});
  if(session.history.length>200){session.history.shift();session.discardedEvents++;}
  session.hintCount=lab.hint;session.cursor=lab.cursor;
  await adapter.saveSession(session);
  if(lab?.session?.id!==session.id)return;
  $('#operation-list').innerHTML=session.history.map(x=>`<li><time>${e(new Date(x.at).toLocaleTimeString())}</time> · ${e(x.details)}</li>`).join('');
  $('#log-count').textContent=String(session.history.length);
}
function metrics(items){return `<div class="metrics">${items.map(([value,label])=>`<div><b>${e(value)}</b><small>${e(label)}</small></div>`).join('')}</div>`;}
function renderFrame(){
  if(!lab?.result)return;
  const frames=lab.result.frames||lab.result.trace,frame=frames[lab.cursor-1];
  $('#step-position').textContent=`${lab.cursor} / ${frames.length} 步`;
  $('#step-back').disabled=lab.cursor===0;$('#step-next').disabled=lab.cursor>=frames.length;
  if(lab.id==='scheduler')renderScheduler(frame);
  if(lab.id==='cache')renderCache(frame);
  if(lab.id==='hash')renderHash(frame);
  if(lab.id==='network')renderNetworkFrame();
  if(lab.cursor>=frames.length){stopPlayer();if(lab.id!=='network')finishObservation();}
}
async function step(direction){
  if(!lab?.result||lab.busy)return;
  lab.busy=true;
  $('#step-next').disabled=true;$('#step-back').disabled=true;$('#playback-controls').setAttribute('aria-busy','true');
  const frames=lab.result.frames||lab.result.trace;lab.cursor=Math.max(0,Math.min(frames.length,lab.cursor+direction));
  lab.session.maxStep=Math.max(lab.session.maxStep,lab.cursor);
  if(lab.id!=='network'&&lab.cursor===frames.length)lab.session.status='observed';
  const sessionId=lab.session.id;
  try{await record('step',`${direction>0?'前进':'回看'}到第 ${lab.cursor} / ${frames.length} 步。`);}
  finally{if(lab?.session?.id===sessionId){lab.busy=false;$('#playback-controls').removeAttribute('aria-busy');renderFrame();}}
}
function renderScheduler(frame){
  const r=lab.result;
  const timeline=`<div class="timeline" aria-label="调度时间线">${r.frames.map((f,i)=>`<div class="time-segment ${f.id===null?'idle':''} ${i===lab.cursor-1?'active':''} ${i>=lab.cursor?'future':''}"><b>${e(i<lab.cursor?(f.id||'空闲'):'待执行')}</b><small>${i<lab.cursor?`${f.start}–${f.end}`:'未观察'}</small></div>`).join('')}</div>`;
  const known=frame?.remaining||lab.session.input.processes.map(p=>({id:p.id,remaining:p.burst}));
  $('#lab-visual').innerHTML=`<h3>CPU 时间线</h3>${timeline}<div class="queue-line"><span>本步结束后的就绪队列</span>${frame?.readyAfter.length?frame.readyAfter.map(id=>`<span class="token">${e(id)}</span>`).join(''):'<span class="muted">空</span>'}</div><table><thead><tr><th>进程</th><th>剩余运行时间</th></tr></thead><tbody>${known.map(p=>`<tr class="${p.id===frame?.id?'active':''}"><td>${e(p.id)}</td><td>${p.remaining}</td></tr>`).join('')}</tbody></table>`;
  $$('.time-segment').forEach((element,index)=>{element.style.flexGrow=String(r.frames[index].end-r.frames[index].start);});
  $('#lab-explanation').innerHTML=frame?`<p>${frame.id?`<strong>${e(frame.id)}</strong> 在 [${frame.start}, ${frame.end}) 使用 CPU。`:`[${frame.start}, ${frame.end}) 没有已到达的就绪进程，CPU 空闲。`}</p>`:'<p>每次单步推进一次分配或空闲区间。未来片段的执行内容会逐步显示。</p>';
  if(lab.cursor===r.frames.length)$('#lab-explanation').innerHTML+=`<table><thead><tr><th>进程</th><th>完成</th><th>周转</th><th>等待</th><th>响应</th></tr></thead><tbody>${r.metrics.map(m=>`<tr><td>${e(m.id)}</td><td>${m.completion}</td><td>${m.turnaround}</td><td>${m.waiting}</td><td>${m.response}</td></tr>`).join('')}</tbody></table>${metrics([[r.averageWaiting.toFixed(2),'平均等待'],[r.averageTurnaround.toFixed(2),'平均周转']])}`;
}
function renderCache(frame){
  const r=lab.result,empty=Array.from({length:r.lines},(_,index)=>({index,valid:false,tag:null,block:null}));
  const bits=frame?frame.binary:'0'.repeat(16),a=r.tagBits,b=r.indexBits;
  const parts=frame?`<div class="address-parts"><div><small>标记 · ${a} 位</small><b>${bits.slice(0,a)||'—'}</b></div><div><small>索引 · ${b} 位</small><b>${bits.slice(a,a+b)||'—'}</b></div><div><small>偏移 · ${r.offsetBits} 位</small><b>${bits.slice(a+b)||'—'}</b></div></div>`:'';
  $('#lab-visual').innerHTML=`<h3>${frame?`访问 0x${frame.address.toString(16).toUpperCase()}（${frame.address}）`:'初始 Cache：所有行均无效'}</h3>${parts}<table><thead><tr><th>行索引</th><th>有效位</th><th>标记</th><th>内存块 / 字节范围</th></tr></thead><tbody>${(frame?.table||empty).map(row=>`<tr class="${row.index===frame?.index?'active':''}"><td>${row.index}</td><td>${row.valid?1:0}</td><td>${row.tag??'—'}</td><td>${row.valid?`${row.block} / ${row.block*r.blockSize}～${(row.block+1)*r.blockSize-1}`:'—'}</td></tr>`).join('')}</tbody></table>${metrics([[frame?.hits||0,'目前命中'],[lab.cursor-(frame?.hits||0),'目前缺失'],[lab.cursor?((frame.hits/lab.cursor)*100).toFixed(1)+'%':'—','目前命中率']])}`;
  $('#lab-explanation').innerHTML=frame?`<p><strong>${frame.hit?'命中':'缺失'}</strong>：块号 ${frame.block}，索引 ${frame.index}，标记 ${frame.tag}，偏移 ${frame.offset}。${frame.evictedBlock!==null?`原内存块 ${frame.evictedBlock} 被覆盖。`:frame.hit?'有效位为真且标记一致。':'该行未含所需内存块，装入后有效。'}</p>`:'<p>同一内存块内的不同字节可命中；映射到同一行的其他块会改变该行内容。</p>';
}
function renderHash(frame){
  const size=lab.result.size,table=frame?.table||Array(size).fill(null);
  $('#lab-visual').innerHTML=`<h3>${frame?`${frame.kind==='insert'?'插入':'查找'} ${frame.key}`:'空散列表'}</h3><div class="hash-slots" data-size="${size}">${table.map((value,index)=>`<div class="hash-slot ${frame?.path.includes(index)?'visited':''} ${index===frame?.index?'active':''}"><small>槽 ${index}</small><b>${value??'空'}</b></div>`).join('')}</div>${metrics([[frame?.count||0,'已存键数'],[((frame?.count||0)/size).toFixed(2),'装载因子'],[frame?.probes||0,'本操作探测次数']])}`;
  const names={probing:'冲突，继续探测',inserted:'空槽插入成功',found:'找到目标键',present:'已有相同键，不重复插入',full:'表满，有限次检查后终止','not-found':'未找到目标键'};
  $('#lab-explanation').innerHTML=frame?`<p>h(${frame.key})=${frame.key}%${size}=${frame.home}。当前检查槽 <strong>${frame.index}</strong>，原值 ${frame.previous??'空'}。${names[frame.status]}。</p><p>已探测：${frame.path.join('，')}。</p>`:'<p>每次单步检查一个槽；最多检查表长个槽，不会在满表中无限循环。</p>';
}
function networkSVG(){
  if(!lab.scenario)return '';
  const s=lab.scenario,visible=lab.result?.trace?.slice(0,lab.cursor)||[],seen=new Set(visible.map(t=>t.node));
  const pos={client:[65,135],r1:[200,135],r2:[355,135],dns:[500,55],web:[500,210]};
  return `<svg class="network-topology" viewBox="0 0 570 285" role="img" aria-label="办公网经 R1、R2 到 DNS 和 Web 服务器的拓扑"><path class="wire" d="M90 135H175M225 135H330M380 135H420V55H475M420 135V210H475"/><text class="subnet-label" x="70" y="30">办公网 ${e(s.office)}</text><text class="subnet-label" x="230" y="95">互联 ${e(s.transit)}</text><text class="subnet-label" x="385" y="278">服务器网 ${e(s.servers)}</text>${s.nodes.map(n=>{
    const [x,y]=pos[n.id],ip=n.interfaces[0].ip,prefix=n.id==='client'?lab.config.prefix:n.interfaces[0].prefix;
    return `<g><rect class="node ${seen.has(n.id)?'visited':''}" x="${x-25}" y="${y-22}" width="50" height="44" rx="8"/><text x="${x}" y="${y+4}" text-anchor="middle">${e(n.id==='client'?'PC':n.name)}</text><text x="${x}" y="${y+41}" text-anchor="middle">${e(ip)}/${prefix}</text>${n.interfaces.length>1?`<text class="subnet-label" x="${x}" y="${y+59}" text-anchor="middle">${e(n.interfaces[1].ip)}</text>`:''}</g>`;
  }).join('')}</svg>`;
}
function renderNetworkConfig(enabled){
  const c=lab.config,s=lab.scenario;
  $('#network-config').innerHTML=`<form id="network-settings" class="network-config"><h3>${e(s.title)}</h3><p class="muted small-text">目标：访问 ${e(s.domain)}。配置与拓扑可对照检查。R2 的返回路由已配置为经 R1 回办公网。</p><div class="inline-fields"><div><label for="config-prefix">电脑前缀长度</label><input id="config-prefix" name="prefix" type="number" min="0" max="32" value="${c.prefix}" ${enabled?'':'disabled'} required></div><div><label for="config-gateway">电脑默认网关</label><input id="config-gateway" name="gateway" value="${e(c.gateway)}" ${enabled?'':'disabled'} required></div><div><label for="config-dns">电脑 DNS 地址</label><input id="config-dns" name="dns" value="${e(c.dns)}" ${enabled?'':'disabled'} required></div></div><details><summary>R1 的静态路由</summary><label class="check-label"><input id="config-route-enabled" name="enabled" type="checkbox" ${c.route.enabled?'checked':''} ${enabled?'':'disabled'}>启用这条路由</label><div class="inline-fields"><div><label for="config-route-destination">目的网段</label><input id="config-route-destination" name="destination" value="${e(c.route.destination)}" ${enabled?'':'disabled'} required></div><div><label for="config-route-hop">下一跳</label><input id="config-route-hop" name="nextHop" value="${e(c.route.nextHop)}" ${enabled?'':'disabled'} required></div></div></details><div class="button-row"><button class="button small" type="submit" ${enabled?'':'disabled'}>应用配置</button>${[['gateway','检查网关'],['server','ping 服务器 IP'],['dns','查询 DNS'],['path','检查路径'],['web','访问网站并验证']].map(([tool,label])=>`<button class="button quiet small" type="button" data-probe="${tool}" ${enabled?'':'disabled'}>${label}</button>`).join('')}</div></form>`;
}
async function applyNetworkConfig(){
  try{
    const f=new FormData($('#network-settings'));
    const next={prefix:Number(f.get('prefix')),gateway:String(f.get('gateway')).trim(),dns:String(f.get('dns')).trim(),
      route:{enabled:f.has('enabled'),destination:String(f.get('destination')).trim(),nextHop:String(f.get('nextHop')).trim()}};
    probeNetwork(lab.scenario,next,'gateway'); // Validate all fields, without accepting probe success as repair.
    lab.config=next;lab.result=null;lab.cursor=0;lab.session.finalConfig=copy(next);lab.session.status='investigating';lab.session.verified=false;lab.session.lastResult=null;
    $('#experiment-review').hidden=true;$('#playback-controls').hidden=true;$('#lab-visual').innerHTML=networkSVG();
    $('#experiment-state').textContent='配置已应用，等待验证';$('#lab-explanation').innerHTML='<p>配置变更已记录。请重新诊断，确认请求与回复路径。</p>';message('#experiment-error','');
    await record('configuration','应用配置：'+JSON.stringify(next));
  }catch(error){message('#experiment-error',error.message);}
}
async function networkProbe(tool){
  try{
    stopPlayer();const result=probeNetwork(lab.scenario,lab.config,tool);lab.result=result;lab.cursor=1;
    lab.session.lastResult=copy(result);lab.session.finalConfig=copy(lab.config);
    if(tool==='web'){lab.session.status=result.ok?'repaired':'investigating';lab.session.verified=!!result.ok;}
    $('#playback-controls').hidden=false;$('#experiment-state').textContent=result.ok?'本项检查成功':'发现需要解释的现象';
    renderFrame();await record('probe',`${tool}：${result.message}`);
    if(tool==='web')renderReview();
  }catch(error){message('#experiment-error',error.message);}
}
function renderNetworkFrame(){
  const r=lab.result;$('#lab-visual').innerHTML=networkSVG();
  $('#lab-explanation').innerHTML=`<p class="network-result ${r.ok?'success':''}">${e(r.message)}</p><ol class="trace-list">${r.trace.map((t,i)=>`<li class="${i===lab.cursor-1?'active':''} ${i>=lab.cursor?'future':''}"><strong>${e(t.phase ? t.phase + " · " : "")}${e(t.name)}</strong>：${i<lab.cursor?e(t.message):'待观察'}</li>`).join('')}</ol><p class="muted small-text">显示的是逻辑转发依据，不是实测网络延迟或真实抓包。</p>`;
}
function finishObservation(){lab.session.status='observed';lab.session.maxStep=lab.result.frames.length;$('#experiment-state').textContent='已观察完整过程';renderReview();}
function summaryText(session){
  try{return checkedSummary(session);}catch{return '记录参数暂时无法复算，可下载原始过程检查。';}
}
function checkedSummary(session){
  if(session.labId==='network')return session.verified?'按域名访问成功，已验证请求与回复路径。':'网站尚未验证修复，请根据诊断继续排查。';
  if(session.status!=='observed')return '尚未观察完整过程。';
  if(session.labId==='scheduler'){const r=simulateSchedule(session.input.processes,session.input);return `平均等待 ${r.averageWaiting.toFixed(2)}；平均周转 ${r.averageTurnaround.toFixed(2)}。`;}
  if(session.labId==='cache'){const r=simulateCache(session.input.addresses,session.input);return `命中 ${r.hits} 次，缺失 ${r.misses} 次，命中率 ${(100*r.hitRate).toFixed(1)}%。`;}
  const r=simulateHash(session.input.operations,session.input);return `存入 ${r.count} 个键，装载因子 ${r.loadFactor.toFixed(2)}；各次探测结果可由输入复现。`;
}
function renderReview(){
  $('#experiment-review').hidden=false;
  $('#review-summary').innerHTML=`<p><strong>${e(summaryText(lab.session))}</strong></p><p>原先预测：${e(lab.session.prediction)}</p><p class="muted">使用 ${lab.hint} 层提示。比较预测与证据，再换一个变式检查能否独立解释。</p>`;
}
async function nextHint(){
  if(!lab?.session){toast('先提交预测并开始实验，再使用提示。');return;}
  const hints=lab.id==='network'?networkHints[lab.scenario.fault]:LABS[lab.id].hints;
  if(lab.hint>=hints.length)return;
  $('#hint-text').textContent=hints[lab.hint++];$('#next-hint').disabled=lab.hint>=hints.length;
  await record('hint',`使用第 ${lab.hint} 层提示：${hints[lab.hint-1]}`);if(!$('#experiment-review').hidden)renderReview();
}
function variant(){
  if(!lab)return;lab.seed=lab.seed%1000000+1;
  if(lab.id==='network'){$('#network-task').value='transfer';$('#experiment-seed').value=String(lab.seed);}
  if(lab.id==='scheduler')$('#schedule-jobs').value=`A,0,${3+lab.seed%5}\nB,${lab.seed%3},${1+(lab.seed*3)%5}\nC,${2+lab.seed%4},${1+lab.seed%4}`;
  if(lab.id==='cache'){const base=(lab.seed%10)*4;$('#cache-addresses').value=[base,base+1,base+16,base,base+4,base+5].join(',');}
  if(lab.id==='hash'){const m=Number($('#hash-size').value),key=1+lab.seed%19;$('#hash-operations').value=`+${key},+${key+m},+${key+2*m},?${key+m},?${key+3*m}`;}
  $('#prediction').value='';invalidateExperiment();toast('已换一个可复现的变式，请重新预测。');
}
async function resumeExperiment(){
  const s=copy(lab.savedSession);if(!s)return;
  try{
    const input=s.input;
    let result;
    if(lab.id==='scheduler')result=simulateSchedule(input.processes,input);
    else if(lab.id==='cache')result=simulateCache(input.addresses,input);
    else if(lab.id==='hash')result=simulateHash(input.operations,input);
    else{
      lab.scenario=createNetworkScenario(input.kind,input.seed);lab.config=copy(s.finalConfig||lab.scenario.config);
      result=s.lastResult?probeNetwork(lab.scenario,lab.config,s.lastResult.tool):null;
    }
    lab.result=result;lab.session=s;lab.seed=s.seed;lab.hint=s.hintCount||0;
    const frames=result?.frames||result?.trace||[];lab.cursor=Math.max(0,Math.min(frames.length,Number.isInteger(s.cursor)?s.cursor:0));
    if(lab.id==='scheduler'){$('#schedule-algorithm').value=input.algorithm;$('#schedule-quantum').value=String(input.quantum||2);$('#schedule-jobs').value=input.processes.map(p=>`${p.id},${p.arrival},${p.burst}`).join('\n');}
    if(lab.id==='cache'){$('#cache-lines').value=String(input.lines);$('#cache-block').value=String(input.blockSize);$('#cache-addresses').value=input.addresses.join(',');}
    if(lab.id==='hash'){$('#hash-size').value=String(input.size);$('#hash-operations').value=input.operations.map(o=>(o.kind==='insert'?'+':'?')+o.key).join(',');}
    if(lab.id==='network'){$('#network-task').value=input.kind;$('#experiment-seed').value=String(input.seed);renderNetworkConfig(true);$('#lab-visual').innerHTML=networkSVG();}
    $('#prediction').value=s.prediction;$('#prediction').readOnly=true;$('#reflection').value=s.reflection||'';
    $('#resume-experiment').hidden=true;$('#playback-controls').hidden=!result;
    if(result)renderFrame();else $('#lab-explanation').innerHTML='<p>已恢复配置和操作记录，请选择诊断工具继续。</p>';
    if(s.status==='observed'||s.lastResult?.tool==='web')renderReview();
    $('#experiment-state').textContent='已恢复上次实验';$('#variant-label').textContent=`变式种子 ${lab.seed} · 模型版本 1`;
    const hints=lab.id==='network'?networkHints[lab.scenario.fault]:LABS[lab.id].hints;
    $('#hint-text').textContent=lab.hint?hints[Math.min(lab.hint,hints.length)-1]:'先观察现象，再决定检查哪一项。';
    $('#next-hint').disabled=lab.hint>=hints.length;
    await record('restore','从当前浏览器恢复上次实验，继续原有过程。');
  }catch(error){toast('上次记录无法恢复：'+error.message,true);}
}
function report(session){
  return `# 知章 · ${LABS[session.labId]?.title||session.labId}\n\n时间：${session.createdAt}\n模型版本：${session.engineVersion}；种子：${session.seed}\n\n## 模型范围\n\n${LABS[session.labId]?.model||''}\n\n## 预测\n\n${session.prediction}\n\n## 输入\n\n\`\`\`json\n${JSON.stringify(session.input,null,2)}\n\`\`\`\n\n${session.finalConfig?'## 最后配置\n\n```json\n'+JSON.stringify(session.finalConfig,null,2)+'\n```\n\n':''}## 观察与验证\n\n${summaryText(session)}\n\n提示层数：${session.hintCount}\n\n## 操作记录\n\n${session.history.map(x=>`- ${x.at}：${x.details}`).join('\n')}\n${session.discardedEvents?`\n较早 ${session.discardedEvents} 条操作已截断。\n`:''}\n## 自己的复盘\n\n${session.reflection||'尚未填写'}\n\n本记录反映教学模型中的操作与结果，不是实机验收或学习效果证明。\n`;
}
async function recordsScreen(){
  if(!await flushNotes())return;
  const epoch=++navigationEpoch;
  await refreshProgress();const sessions=await adapter.sessions();if(epoch!==navigationEpoch)return;showScreen('records');setRoute('records');selectNav(null);
  const entries=Object.entries(progress).filter(([id])=>lessonMap.has(id));
  $('#record-summary').innerHTML=[[entries.length,'已打开课时'],[entries.filter(([,r])=>r.completed).length,'已标记学完'],[sessions.length,'保留的实验过程']].map(([n,title])=>`<div class="summary-item"><b>${n}</b><span>${title}</span></div>`).join('');
  $('#record-lessons').innerHTML=entries.length?entries.map(([id])=>{const r=stat(id),l=lessonMap.get(id);return `<article class="record-item"><div><h3>${e(l.lesson.title)}</h3><p>${e(l.course.title)} · 已答 ${r.attempted} 题，最近答对 ${r.correct} 题${r.completed?' · 已标记学完':''}</p></div><button class="button small" data-lesson="${e(id)}" ${r.attempted>r.correct?'data-review="1"':''}>${r.attempted>r.correct?'复习本节错题':'继续学习'}</button></article>`;}).join(''):'<p class="empty-state">还没有打开课时。选择一门课开始吧。</p>';
  $('#record-sessions').innerHTML=sessions.length?[...sessions].reverse().map(s=>`<article class="record-item"><div><h3>${e(LABS[s.labId]?.title||'实验记录')}</h3><p>${e(new Date(s.createdAt).toLocaleString())} · ${e(summaryText(s))}</p></div><button class="button small" data-session="${e(s.id)}">下载过程记录</button></article>`).join(''):'<p class="empty-state">完成一次预测并开始实验后，这里会出现过程记录。</p>';
}

document.addEventListener('click',async event=>{
  const button=event.target.closest('button,a');if(!button||button.disabled)return;
  try{
    if(button.matches('.brand')){event.preventDefault();await goHome();}
    if(button.matches('.reading-anchors a')){event.preventDefault();$(button.getAttribute('href'))?.scrollIntoView({block:'start'});}
    if(button.dataset.subject)await goSubject(button.dataset.subject);
    if(button.dataset.lesson)await openLesson(button.dataset.lesson,{focusQuiz:!!button.dataset.review});
    if(button.dataset.lab)await openLab(button.dataset.lab);
    if(button.dataset.probe&&lab?.session)await networkProbe(button.dataset.probe);
    if(button.dataset.session){const s=(await adapter.sessions()).find(s=>s.id===button.dataset.session);if(s)jsonDownload(s,`知章-实验-${s.labId}.json`);}
    switch(button.id){
      case 'records-button':await recordsScreen();break;
      case 'export-data':if(await flushNotes()){download(await adapter.exportData(),'知章-课时学习记录.json');toast(localMode?'课时备份已导出；实验记录请在实验或学习记录页另行下载。':'学习和实验记录已导出。');}break;
      case 'save-notes':await saveNotes();break;
      case 'download-draft':download(new Blob([$('#lesson-notes').value],{type:'text/plain;charset=utf-8'}),'知章-笔记草稿.txt');break;
      case 'reload-notes':{
        const fresh=await adapter.open(currentLesson.id);currentLesson.notes_revision=fresh.notes_revision;
        $('#lesson-notes').value=fresh.notes;notesDirty=false;message('#notes-error','');$('#notes-state').textContent='已读取保存版本';$('#reload-notes').hidden=true;break;
      }
      case 'complete-lesson':if(await flushNotes()){await adapter.progress(currentLesson.id,!currentLesson.completed);currentLesson.completed=!currentLesson.completed;$('#complete-lesson').textContent=currentLesson.completed?'已标记学完 · 撤销标记':'标记学完';await refreshProgress();renderOutline();}break;
      case 'previous-lesson':case 'next-lesson':{const index=currentPath.lessons.findIndex(l=>l.id===currentLesson.id)+(button.id==='previous-lesson'?-1:1);if(currentPath.lessons[index])await openLesson(currentPath.lessons[index].id);break;}
      case 'lab-to-course':await goSubject(LABS[lab.id].subject);break;
      case 'step-next':await step(1);break;
      case 'step-back':stopPlayer();await step(-1);break;
      case 'auto-play':if(player)stopPlayer();else if(lab?.result){if(lab.cursor>=(lab.result.frames||lab.result.trace).length)lab.cursor=0;$('#auto-play').textContent='暂停';player=setInterval(()=>step(1).catch(error=>{stopPlayer();toast(error.message,true);}),850);}break;
      case 'next-hint':await nextHint();break;
      case 'variant':variant();break;
      case 'resume-experiment':await resumeExperiment();break;
      case 'reset-experiment':invalidateExperiment();break;
      case 'save-reflection':if(lab?.session){lab.session.reflection=$('#reflection').value;await record('reflection','保存自己的复盘。');toast('复盘已保存。');}break;
      case 'download-report':if(lab?.session){lab.session.reflection=$('#reflection').value;await adapter.saveSession(lab.session);download(new Blob([report(lab.session)],{type:'text/markdown;charset=utf-8'}),`知章-${lab.id}-实验报告.md`);}break;
      case 'download-session':if(lab?.session){lab.session.reflection=$('#reflection').value;await adapter.saveSession(lab.session);jsonDownload(lab.session,`知章-${lab.id}-实验过程.json`);}break;
      case 'retry-boot':location.reload();break;
    }
  }catch(error){toast(error.message,true);}
});
document.addEventListener('submit',async event=>{
  const form=event.target;
  if(form.id==='experiment-form'){event.preventDefault();await startExperiment();return;}
  if(form.id==='network-settings'){event.preventDefault();await applyNetworkConfig();return;}
  if(form.matches('[data-quiz]')){
    event.preventDefault();const index=Number(form.dataset.quiz),choice=Number(new FormData(form).get('q'+index)),id=currentLesson.id;
    const submit=form.querySelector('button');submit.disabled=true;
    try{const result=await adapter.attempt(id,index,choice);if(currentLesson?.id===id){showQuizResult(index,result);currentLesson.attempts=(currentLesson.attempts||[]).filter(a=>a.question_index!==index).concat([{question_index:index,choice,correct:result.correct}]);await refreshProgress();renderOutline();}}
    catch(error){toast(error.message,true);}finally{submit.disabled=false;}
  }
});
$('#path-select').addEventListener('change',async()=>{const selected=$('#path-select').value;const path=bundle.courses.find(c=>c.id===selected);if(path)await openLesson(path.lessons[0].id);});
$('#lesson-search').addEventListener('input',()=>{if(currentPath)renderOutline();});
document.addEventListener('input',event=>{if(event.target.id==='lesson-notes'){notesDirty=true;$('#notes-state').textContent='有未保存修改';}});
$('#experiment-inputs').addEventListener('change',()=>{
  if(lab?.id==='scheduler')$('#schedule-quantum').disabled=$('#schedule-algorithm').value==='fcfs';
  if(lab)invalidateExperiment();
});
window.addEventListener('beforeunload',event=>{if(notesDirty){event.preventDefault();event.returnValue='';}});
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopPlayer();});
window.addEventListener('hashchange',async()=>{
  if(!adapter)return;
  try{
    const hash=location.hash.slice(1);
    if(hash==='home')await goHome();
    else if(hash==='records')await recordsScreen();
    else if(hash.startsWith('lab/')&&LABS[hash.slice(4)])await openLab(hash.slice(4));
    else if(hash.startsWith('lesson/')&&lessonMap.has(decodeURIComponent(hash.slice(7))))await openLesson(decodeURIComponent(hash.slice(7)));
  }catch(error){toast(error.message,true);}
});

async function boot(){
  try{
    const response=await fetch('courses.json');if(!response.ok)throw new Error('课程内容读取失败，请刷新或检查网络。');
    bundle=await response.json();
    if(bundle.version!==1||!Array.isArray(bundle.courses))throw new Error('课程包版本不支持。');
    lessonMap=new Map(bundle.courses.flatMap(course=>course.lessons.map(lesson=>[lesson.id,{lesson,course}])));
    localMode=new URLSearchParams(location.search).get('local')==='1';
    adapter=new (localMode?LocalAdapter:BrowserAdapter)(bundle,storageWarning);
    if(localMode)await adapter.request('/courses');
    if(adapter.persistent)$('#storage-mode').textContent=localMode?'本地完整版 · 课时存入学习数据库，实验保存在当前浏览器':'无需账号与模型密钥 · 学习记录保存在当前浏览器';
    $('#subject-nav').innerHTML=bundle.subjects.map(s=>`<button data-subject="${e(s.id)}">${e(s.short)}</button>`).join('');
    await refreshProgress();renderHome();
    const hash=location.hash.slice(1);
    if(hash.startsWith('lesson/')&&lessonMap.has(decodeURIComponent(hash.slice(7))))await openLesson(decodeURIComponent(hash.slice(7)));
    else if(hash.startsWith('lab/')&&LABS[hash.slice(4)])await openLab(hash.slice(4));
    else if(hash==='records')await recordsScreen();
  }catch(error){showScreen('fatal');$('#fatal-message').textContent=error.message+(localMode?' 请从正在运行的学习助手入口进入本地模式。':'');}
}
await boot();
