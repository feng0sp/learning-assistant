const KEY = 'zhizhang-academy-v1';
const blank = () => ({version: 1, records: {}, sessions: [], current: null});
const clone = value => structuredClone(value);

export class BrowserAdapter {
  constructor(bundle, warning = () => {}) {
    this.bundle = bundle; this.warning = warning; this.memory = blank(); this.persistent = true;
    this.lessons = new Map(bundle.courses.flatMap(c => c.lessons.map(l => [l.id, {lesson: l, course: c}])));
    this.read();
  }
  unavailable(message) {
    if (this.persistent) this.warning(message);
    this.persistent = false;
  }
  read() {
    if (!this.persistent) return clone(this.memory);
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return clone(this.memory);
      const value = JSON.parse(raw);
      if (value.version !== 1 || !value.records || typeof value.records !== 'object' || Array.isArray(value.records) ||
          !Array.isArray(value.sessions)) throw new Error('存档版本或格式不支持');
      const records = {};
      for (const [id, r] of Object.entries(value.records)) {
        if (!this.lessons.has(id)) continue;
        if (!r || typeof r.notes !== 'string' || r.notes.length > 30000 || !Number.isSafeInteger(r.notes_revision) ||
            r.notes_revision < 0 || typeof r.completed !== 'boolean' || !Array.isArray(r.attempts) || r.attempts.length > 12) throw new Error('学习记录损坏');
        const quiz = this.lessons.get(id).lesson.guide.quiz;
        const attempts = r.attempts.filter(a => Number.isInteger(a.question_index) && a.question_index >= 0 &&
          a.question_index < quiz.length && Number.isInteger(a.choice) && a.choice >= 0 && a.choice < 4)
          .map(a => ({question_index:a.question_index, choice:a.choice, correct:a.choice === quiz[a.question_index].answer}));
        records[id] = {notes:r.notes, notes_revision:r.notes_revision, completed:r.completed, attempts,
          lastViewed: Number.isFinite(r.lastViewed) ? r.lastViewed : 0};
      }
      this.memory = {version:1, records, sessions:value.sessions.slice(-20), current:this.lessons.has(value.current) ? value.current : null};
    } catch {
      this.unavailable('浏览器存档无法读取。当前可继续临时学习，请及时导出；原存档未被覆盖。');
    }
    return clone(this.memory);
  }
  async mutate(action) {
    const update = () => {
      const value = this.read(), result = action(value);
      this.memory = clone(value);
      if (this.persistent) {
        try { localStorage.setItem(KEY, JSON.stringify(value)); }
        catch { this.unavailable('浏览器存储不可用或空间不足，当前改用临时记录；关闭页面前请导出。'); }
      }
      return result;
    };
    return navigator.locks ? navigator.locks.request(KEY + '-write', update) : update();
  }
  ensure(value, id) {
    if (!this.lessons.has(id)) throw new Error('课时不存在。');
    return value.records[id] ??= {notes:'',notes_revision:0,completed:false,attempts:[],lastViewed:0};
  }
  async open(id) {
    return this.mutate(value => {
      const r = this.ensure(value,id); r.lastViewed=Date.now(); value.current=id;
      return {...clone(this.lessons.get(id).lesson), ...clone(r)};
    });
  }
  async saveNotes(id, notes, revision) {
    if (notes.length > 30000) throw new Error('笔记最多 30000 字。');
    return this.mutate(value => {
      const r=this.ensure(value,id);
      if(r.notes_revision!==revision) throw new Error('另一窗口已修改笔记。当前草稿已保留，可先下载草稿，再读取已保存版本。');
      r.notes=notes; r.notes_revision++;
      return {revision:r.notes_revision};
    });
  }
  async attempt(id, index, choice) {
    return this.mutate(value => {
      const r=this.ensure(value,id), q=this.lessons.get(id).lesson.guide.quiz[index];
      if(!q || !Number.isInteger(choice) || choice<0 || choice>3) throw new Error('请选择有效选项。');
      const correct=q.answer===choice;
      r.attempts=r.attempts.filter(a=>a.question_index!==index).concat([{question_index:index,choice,correct}]);
      return {correct,answer:q.answer,explanation:q.explanation};
    });
  }
  async progress(id, completed) { return this.mutate(value=>{this.ensure(value,id).completed=completed;}); }
  async summaries() { return this.read().records; }
  async saveSession(session) {
    return this.mutate(value=>{
      value.sessions=value.sessions.filter(s=>s.id!==session.id).concat([clone(session)]).slice(-20);
    });
  }
  async sessions() { return this.read().sessions; }
  async exportData() { return new Blob([JSON.stringify({...this.read(), exportedAt:new Date().toISOString()},null,2)], {type:'application/json'}); }
}

export class LocalAdapter extends BrowserAdapter {
  async request(path, options = {}) {
    const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
    if(!response.ok) {
      const error=await response.json().catch(()=>({}));
      throw new Error(typeof error.detail==='string'?error.detail:'本地服务请求失败，请检查学习助手是否正在运行。');
    }
    return response.json();
  }
  async open(id) {
    const info=this.lessons.get(id);
    if(!info) throw new Error('课时不存在。');
    await this.request(`/course/${info.course.id}/lessons/${id}/open`,{method:'POST'});
    const chapter=await this.request(`/chapters/${id}`);
    return {...info.lesson,...chapter};
  }
  async saveNotes(id,notes,revision) {
    const result=await this.request(`/chapters/${id}/notes`,{method:'PUT',body:JSON.stringify({notes,expected_revision:revision})});
    return {revision:result.notes_revision};
  }
  async attempt(id,index,choice) { return this.request(`/chapters/${id}/attempts`,{method:'POST',body:JSON.stringify({question_index:index,choice})}); }
  async progress(id,completed) { return this.request(`/chapters/${id}/progress`,{method:'PUT',body:JSON.stringify({completed})}); }
  async summaries() {
    const catalogs=await Promise.all(this.bundle.courses.map(c=>this.request(`/course/${c.id}`)));
    return Object.fromEntries(catalogs.flatMap(c=>c.lessons.filter(l=>l.started).map(l=>[l.id,l])));
  }
  async exportData() {
    const response=await fetch('/api/backups/export');
    if(!response.ok) throw new Error('本地备份导出失败。');
    return response.blob();
  }
}
