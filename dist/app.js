'use strict';

const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {chapter: null, chapters: [], tab: 'knowledge', material: null, settings: null, settingsEpoch: 0, uploading: false, asking: false, loading: 0, job: null, polling: null, notesDirty: false, notesTimer: null, notesQueue: Promise.resolve(), noteRevisions: {}, notesConflict: false, quizChoices: {}, reviewItems: [], reviewChoices: {}, reviewSaving: false, reviewEpoch: 0};
const questionDrafts = new Map();
const pendingQuiz = new Set();
const pendingProgress = new Set();
const quizChoiceRevisions = new Map();
const modeLabels = {demo: '教学示例', material: '根据资料整理', topic: '通用讲解'};
let toastTimer;
let materialPreviewEpoch = 0;
let materialUploadEpoch = 0;
let materialUploadController = null;
let failedChapterId = null;
let studyLoadEpoch = 0;
let historyEpoch = 0;
let historyPage = 0;
const HISTORY_PAGE_SIZE = 50;
const MESSAGE_BATCH_SIZE = 30;
let messageLimit = MESSAGE_BATCH_SIZE;
const pendingMessagePages = new WeakSet();
const REVIEW_PAGE_SIZE = 10;
let reviewPage = 0;
let draftTimer = null;
let openingSettings = false;
let savingSettings = false;
let settingsEditRevision = 0;
const backupState = {file: null, preview: null, busy: false};

async function api(path, options = {}) {
  const headers = {...options.headers};
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  let response;
  try { response = await fetch('/api' + path, {...options, headers}); }
  catch { throw new Error('无法连接本地服务。请确认启动窗口仍在运行，然后重试。'); }
  let data;
  try { data = await response.json(); }
  catch { throw new Error('服务返回了无法识别的结果，请重试。'); }
  if (!response.ok) {
    let detail = data.detail || '操作失败，请重试。';
    if (Array.isArray(detail)) detail = detail.map(item => `${item.loc?.slice(1).join('.') || '输入'}：${item.msg}`).join('\n');
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return data;
}

function showToast(message, error = false) {
  clearTimeout(toastTimer);
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.toggle('error', error);
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, error ? 7000 : 3500);
}

function showMessage(selector, message) {
  const element = $(selector);
  element.textContent = message;
  element.hidden = !message;
}

function showLoadError(message, chapterId = null) {
  failedChapterId = chapterId;
  $('#load-error-message').textContent = message;
  $('#load-error').hidden = !message;
}

function toggleNavigation(open) {
  $('.sidebar').classList.toggle('navigation-open', open);
  $('#toggle-navigation').setAttribute('aria-expanded', String(open));
  $('#navigation-toggle-label').textContent = open ? '收起 ↑' : '展开 ↓';
}

function setBackupBusy(busy, message = '') {
  backupState.busy = busy;
  $('#backup-export').disabled = busy;
  $('#backup-file').disabled = busy;
  $('#backup-import').disabled = busy || !backupState.file || !backupHasChanges();
  $('#backup-status').textContent = message;
}

function backupHasChanges() {
  return ['new', 'copies', 'rewards_new', 'practice_days_new'].some(key => (backupState.preview?.[key] ?? 0) > 0);
}

function openData() {
  if (backupState.busy) { showToast('正在处理学习备份，请稍等。'); return; }
  backupState.file = null;
  backupState.preview = null;
  $('#backup-file').value = '';
  $('#backup-preview').hidden = true;
  showMessage('#backup-error', '');
  showMessage('#backup-success', '');
  setBackupBusy(false);
  if (!$('#data-dialog').open) $('#data-dialog').showModal();
}

async function exportBackup() {
  if (backupState.busy) return;
  setBackupBusy(true, '正在保存笔记并准备备份…');
  showMessage('#backup-error', '');
  showMessage('#backup-success', '');
  try {
    while (state.notesDirty) await saveNotes(true);
    const document = await api('/backups/export');
    const url = URL.createObjectURL(new Blob([JSON.stringify(document, null, 2)], {type: 'application/json;charset=utf-8'}));
    const link = window.document.createElement('a');
    link.href = url;
    link.download = '知章学习备份.json';
    window.document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    showMessage('#backup-success', `已准备 ${document.chapters.length} 章的备份，请在浏览器下载列表中确认文件已保存。`);
  } catch (error) {
    showMessage('#backup-error', (state.notesDirty ? '当前笔记尚未保存，请关闭本窗口处理后重试。' : '') + error.message);
  } finally { setBackupBusy(false); }
}

async function previewBackup() {
  if (backupState.busy) return;
  const file = $('#backup-file').files[0];
  backupState.file = null;
  backupState.preview = null;
  $('#backup-preview').hidden = true;
  showMessage('#backup-error', '');
  showMessage('#backup-success', '');
  if (!file) { setBackupBusy(false); return; }
  if (file.size > 30 * 1024 * 1024) {
    showMessage('#backup-error', '备份文件不能超过 30 MB。');
    setBackupBusy(false);
    return;
  }
  setBackupBusy(true, '正在检查备份内容与已有记录…');
  const body = new FormData();
  body.append('file', file);
  try {
    const result = await api('/backups/preview', {method: 'POST', body});
    backupState.file = file;
    backupState.preview = result;
    $('#backup-preview-summary').textContent = `共 ${result.total} 章：新增 ${result.new} 章，冲突副本 ${result.copies} 章，跳过 ${result.skipped} 章。`;
    $('#backup-rewards-summary').textContent = result.format_version === 2
      ? `成长记录：新增 ${result.rewards_new} 项奖励（${result.points_new} 分），${result.practice_days_new} 条每日练习；已有奖励不会重复增加。`
      : '这是一份旧版备份，不含成长记录；导入不会修改现有积分。';
    const labels = {new: '新增', copy: '导入副本', skip: '已存在'};
    $('#backup-preview-list').innerHTML = result.chapters.map(chapter => `<li><span>${escapeHTML(chapter.course)} · ${escapeHTML(chapter.title)}</span><span class="badge">${labels[chapter.action]}</span></li>`).join('');
    $('#backup-preview').hidden = false;
    if (!backupHasChanges()) showMessage('#backup-success', '这份备份没有需要新增的记录，无需重复导入。');
  } catch (error) { showMessage('#backup-error', error.message); }
  finally { setBackupBusy(false); }
}

async function importBackup() {
  if (backupState.busy || !backupState.file) return;
  setBackupBusy(true, '正在合并学习记录…');
  showMessage('#backup-error', '');
  showMessage('#backup-success', '');
  const body = new FormData();
  body.append('file', backupState.file);
  try {
    const result = await api('/backups/import', {method: 'POST', body});
    backupState.file = null;
    backupState.preview = null;
    $('#backup-file').value = '';
    $('#backup-preview').hidden = true;
    showMessage('#backup-success', `已导入 ${result.imported} 章（其中冲突副本 ${result.copies} 章），跳过 ${result.skipped} 章。可以关闭本窗口，在侧栏查找导入的章节。`);
    if ((result.rewards_imported ?? 0) || (result.practice_days_imported ?? 0)) {
      $('#backup-success').textContent += ` 已恢复 ${result.rewards_imported} 项奖励（新增 ${result.points_added} 分）和 ${result.practice_days_imported} 条每日练习。`;
    }
    window.dispatchEvent(new Event('learning-data-imported'));
    await refreshHistory();
  } catch (error) { showMessage('#backup-error', error.message); }
  finally { setBackupBusy(false); }
}

function textList(items, tag = 'li') { return items.map(item => `<${tag}>${escapeHTML(item)}</${tag}>`).join(''); }

function sourceText(value) {
  // Escape every fragment before inserting markup. Only literal HTTP(S) links
  // are clickable; source content is never interpreted as HTML or Markdown.
  return String(value).split(/(https?:\/\/[^\s<>"']+)/g).map((part, index) => {
    if (index % 2 === 0) return escapeHTML(part);
    const match = part.match(/^(.*?)([。，；！？、）】]+)?$/u);
    const url = match[1];
    try {
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol)) return escapeHTML(part);
    } catch { return escapeHTML(part); }
    return `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(url)}</a>${escapeHTML(match[2] || '')}`;
  }).join('');
}

function sourceLink(id) {
  const index = state.chapter.sources.findIndex(source => source.id === id);
  if (index < 0) return escapeHTML('[' + id + ']');
  const source = state.chapter.sources[index];
  return `<button type="button" class="source-link" data-source="${escapeHTML(id)}" title="${escapeHTML(source.label)}">资料 ${index + 1}</button>`;
}

function showSource(id) {
  switchTab('sources');
  const source = document.getElementById('source-' + id);
  if (!source) return;
  source.classList.add('highlight');
  source.tabIndex = -1;
  source.focus({preventScroll: true});
  source.scrollIntoView({behavior: 'smooth', block: 'center'});
}

async function refreshHistory() {
  const epoch = ++historyEpoch;
  const chapters = await api('/chapters');
  if (epoch !== historyEpoch) return;
  state.chapters = chapters;
  renderHistory();
}

function renderHistory({revealCurrent = false} = {}) {
  $('#chapter-count').textContent = state.chapters.length;
  $('#review-count').textContent = state.chapters.reduce((total, chapter) => total + chapter.attempted - chapter.correct, 0);
  const terms = $('#chapter-search').value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const filter = $('#chapter-filter').value;
  const visible = state.chapters.filter(chapter => {
    const text = [chapter.course, chapter.textbook, chapter.title].join(' ').toLocaleLowerCase();
    return terms.every(term => text.includes(term)) && (filter === 'all' || Boolean(chapter.completed) === (filter === 'completed'));
  });
  $('#history-result').hidden = !terms.length && filter === 'all';
  $('#history-result').textContent = `显示 ${visible.length} / ${state.chapters.length} 章`;
  if (revealCurrent) {
    const index = visible.findIndex(chapter => chapter.id === state.chapter?.id);
    if (index >= 0) historyPage = Math.floor(index / HISTORY_PAGE_SIZE);
  }
  const pageCount = Math.max(1, Math.ceil(visible.length / HISTORY_PAGE_SIZE));
  historyPage = Math.max(0, Math.min(historyPage, pageCount - 1));
  $('#history-pages').hidden = pageCount <= 1;
  $('#history-page-label').textContent = `第 ${historyPage + 1} / ${pageCount} 页`;
  $('#history-prev').disabled = historyPage === 0;
  $('#history-next').disabled = historyPage === pageCount - 1;
  const focusId = document.activeElement?.dataset.chapter;
  const groups = new Map();
  visible.slice(historyPage * HISTORY_PAGE_SIZE, (historyPage + 1) * HISTORY_PAGE_SIZE).forEach(chapter => {
    if (!groups.has(chapter.course)) groups.set(chapter.course, []);
    groups.get(chapter.course).push(chapter);
  });
  $('#chapter-list').innerHTML = [...groups].map(([course, chapters]) => `<div class="course-label">${escapeHTML(course)}</div>${chapters.map(chapter => `<button type="button" class="chapter-nav ${state.chapter?.id === chapter.id ? 'active' : ''}" data-chapter="${escapeHTML(chapter.id)}" ${state.chapter?.id === chapter.id ? 'aria-current="page"' : ''}>${escapeHTML(chapter.title)}<small>${chapter.completed ? '✓ 已学完' : modeLabels[chapter.mode]}${chapter.attempted ? ` · 自测 ${chapter.correct}/${chapter.attempted}` : ''}</small></button>`).join('')}`).join('');
  if (!visible.length) $('#chapter-list').innerHTML = '<p class="sidebar-empty">没有符合条件的章节。</p><button type="button" class="settings-button" id="clear-history-filters">清除筛选</button>';
  if (focusId) $$('[data-chapter]').find(button => button.dataset.chapter === focusId)?.focus({preventScroll: true});
}

async function openReview() {
  if (state.reviewSaving) { showToast('正在保存本次作答，请稍等。'); return; }
  if (!$('#review-dialog').open) $('#review-dialog').showModal();
  const epoch = ++state.reviewEpoch;
  $('#review-list').setAttribute('aria-busy', 'true');
  $('#review-list').innerHTML = '';
  $('#review-pages').hidden = true;
  $('#review-summary').textContent = '正在读取错题…';
  $('#refresh-review').disabled = true;
  showMessage('#review-error', '');
  try {
    const items = await api('/review');
    if (epoch !== state.reviewEpoch) return;
    state.reviewItems = items;
    reviewPage = 0;
    state.reviewChoices = {};
    renderReview();
    await refreshHistory();
  } catch (error) {
    if (epoch === state.reviewEpoch) {
      showMessage('#review-error', error.message);
      $('#review-summary').textContent = '暂时无法读取，请重试。';
    }
  } finally {
    if (epoch === state.reviewEpoch) {
      $('#review-list').setAttribute('aria-busy', 'false');
      $('#refresh-review').disabled = false;
    }
  }
}

function renderReview() {
  const focused = document.activeElement;
  const choiceFocus = $('#review-list').contains(focused) && focused?.matches('input[name^="review-"]') ? {name: focused.name, value: focused.value} : null;
  const remaining = state.reviewItems.filter(item => !item.result?.correct).length;
  $('#review-summary').textContent = state.reviewItems.length ? `待复习 ${remaining} 题 · 本轮已答对 ${state.reviewItems.length - remaining} 题` : '目前没有待复习的错题';
  const pageCount = Math.max(1, Math.ceil(state.reviewItems.length / REVIEW_PAGE_SIZE));
  reviewPage = Math.max(0, Math.min(reviewPage, pageCount - 1));
  $('#review-pages').hidden = pageCount <= 1;
  $('#review-page-label').textContent = `第 ${reviewPage + 1} / ${pageCount} 页`;
  $('#review-prev').disabled = state.reviewSaving || reviewPage === 0;
  $('#review-next').disabled = state.reviewSaving || reviewPage === pageCount - 1;
  $('#review-list').innerHTML = state.reviewItems.length ? state.reviewItems.slice(reviewPage * REVIEW_PAGE_SIZE, (reviewPage + 1) * REVIEW_PAGE_SIZE).map((item, offset) => {
    const index = reviewPage * REVIEW_PAGE_SIZE + offset;
    const selected = state.reviewChoices[index];
    const feedback = item.result && selected === item.previous_choice ? item.result : null;
    return `<article class="quiz-card review-card" id="review-${index}"><div class="review-source"><span>${escapeHTML(item.course)} · ${escapeHTML(item.title)}</span><span class="badge">${modeLabels[item.mode]}</span></div><p class="field-help">本章第 ${item.question_index + 1} 题 · 上次选择 ${String.fromCharCode(65 + item.previous_choice)}</p><h3 id="review-question-${index}">${escapeHTML(item.question)}</h3><div class="quiz-options" role="radiogroup" aria-labelledby="review-question-${index}">${item.options.map((option, choice) => `<label class="quiz-option ${feedback && choice === feedback.answer ? 'correct' : ''} ${feedback && choice === selected && !feedback.correct ? 'incorrect' : ''}"><input type="radio" name="review-${index}" value="${choice}" ${selected === choice ? 'checked' : ''}><span>${String.fromCharCode(65 + choice)}. ${escapeHTML(option)}</span></label>`).join('')}</div><div class="review-actions"><button type="button" class="button secondary small" data-review-check="${index}" ${state.reviewSaving || pendingQuiz.has(`${item.chapter_id}:${item.question_index}`) ? 'disabled' : ''}>${item.result ? '重新检查' : '检查答案'}</button><button type="button" class="text-button" data-review-open="${index}">返回本章查看 ↗</button></div>${feedback ? `<div class="quiz-feedback ${feedback.correct ? '' : 'wrong'}" role="status"><strong>${feedback.correct ? '已答对，下次不再列为待复习题' : '还需要复习，正确答案是 ' + String.fromCharCode(65 + feedback.answer) + '。'}</strong><p>${escapeHTML(feedback.explanation)}</p></div>` : ''}</article>`;
  }).join('') : '<div class="empty-state"><h3>从章节自测开始</h3><p>在章节里选择答案并点击「检查答案」，答错的题目会汇集到这里。尚未作答的题目不会计入错题。</p></div>';
  if (choiceFocus) $$('input[name^="review-"]', $('#review-list')).find(input => input.name === choiceFocus.name && input.value === choiceFocus.value)?.focus({preventScroll: true});
}

async function reviewAnswer(index) {
  if (state.reviewSaving) return;
  const choice = state.reviewChoices[index];
  if (choice == null) { showMessage('#review-error', '先选一个答案，再检查。'); return; }
  const item = state.reviewItems[index];
  if (!item) return;
  const hadFocus = document.activeElement === $(`[data-review-check="${index}"]`);
  const key = `${item.chapter_id}:${item.question_index}`;
  const choiceRevision = quizChoiceRevisions.get(key) ?? 0;
  if (pendingQuiz.has(key)) { showMessage('#review-error', '这道题正在保存，请稍等。'); return; }
  pendingQuiz.add(key);
  state.reviewSaving = true;
  syncAnswerButtons();
  $('#review-prev').disabled = true;
  $('#review-next').disabled = true;
  $('#refresh-review').disabled = true;
  $$('[data-review-check]').forEach(button => button.disabled = true);
  showMessage('#review-error', '');
  try {
    const result = await api(`/chapters/${encodeURIComponent(item.chapter_id)}/attempts`, {method: 'POST', body: JSON.stringify({question_index: item.question_index, choice})});
    item.previous_choice = choice;
    window.dispatchEvent(new CustomEvent('learning-answer', {detail: result}));
    item.result = result;
    if (state.chapter?.id === item.chapter_id) {
      state.chapter.attempts = state.chapter.attempts.filter(attempt => attempt.question_index !== item.question_index);
      state.chapter.attempts.push({question_index: item.question_index, choice, correct: result.correct});
      if ((quizChoiceRevisions.get(key) ?? 0) === choiceRevision) delete state.quizChoices[item.question_index];
      renderProgress();
      if (state.tab === 'quiz') renderContent();
    }
    await refreshHistory();
  } catch (error) { showMessage('#review-error', error.message); }
  finally {
    pendingQuiz.delete(key);
    state.reviewSaving = false;
    syncAnswerButtons();
    $('#refresh-review').disabled = false;
    renderReview();
    if (hadFocus && $('#review-dialog').open && document.activeElement === document.body) $(`[data-review-check="${index}"]`)?.focus({preventScroll: true});
  }
}

async function saveNotes(silent = false) {
  if (!state.chapter || !state.notesDirty) return;
  clearTimeout(state.notesTimer);
  if (state.notesConflict) {
    const error = new Error('另一窗口修改了笔记，请先合并两份内容。');
    $('#notes-status').textContent = '有另一窗口的修改，请先合并';
    if (!silent) showToast(error.message, true);
    throw error;
  }
  const chapterId = state.chapter.id;
  const content = $('#notes').value;
  $('#notes-status').textContent = '正在保存…';
  const operation = state.notesQueue.catch(() => {}).then(async () => {
    const result = await api(`/chapters/${encodeURIComponent(chapterId)}/notes`, {method: 'PUT', body: JSON.stringify({notes: content, expected_revision: state.noteRevisions[chapterId] ?? 0})});
    state.noteRevisions[chapterId] = result.notes_revision;
    return result;
  });
  state.notesQueue = operation;
  try {
    await operation;
    if (state.chapter?.id === chapterId && $('#notes').value === content && state.notesQueue === operation) {
      state.notesDirty = false;
      state.chapter.notes = content;
      $('#notes-status').textContent = '已保存到本机';
    }
    if (!silent) showToast('笔记已保存');
  } catch (error) {
    if (error.status === 409 && state.chapter?.id === chapterId) {
      state.notesConflict = true;
      $('#merge-notes').hidden = false;
      $('#notes-status').textContent = '有另一窗口的修改，请先合并';
    } else $('#notes-status').textContent = '未保存，请重试';
    if (!silent) showToast(error.message, true);
    throw error;
  }
}

async function openChapter(id, {updateLocation = true} = {}) {
  if (state.chapter?.id === id) {
    ++state.loading;
    $('#study-content').setAttribute('aria-busy', 'false');
    $('#navigation-status').hidden = true;
    showLoadError('');
    return;
  }
  const revision = ++state.loading;
  try { await saveNotes(true); }
  catch (error) { showToast('笔记保存失败，已保留当前页面。' + error.message, true); return; }
  if (revision !== state.loading) return;
  $('#study-content').setAttribute('aria-busy', 'true');
  showLoadError('');
  $('#navigation-status').hidden = false;
  try {
    let chapter;
    try { chapter = await api('/chapters/' + encodeURIComponent(id) + '/study'); }
    catch (error) {
      // A running local server may still have the previous backend loaded.
      if (error.status !== 404) throw error;
      chapter = await api('/chapters/' + encodeURIComponent(id));
    }
    if (revision !== state.loading) return;
    // Keep edits made during either the chapter fetch or a note-save response.
    // The final clean check and chapter replacement share one JS turn.
    while (state.notesDirty && revision === state.loading) await saveNotes(true);
    if (revision !== state.loading) return;
    rememberQuestion();
    state.chapter = chapter;
    try { localStorage.setItem('zhizhang-last-chapter', chapter.id); } catch {}
    messageLimit = MESSAGE_BATCH_SIZE;
    $('#question').value = questionDrafts.get(chapter.id)?.text ?? '';
    state.noteRevisions[chapter.id] = chapter.notes_revision ?? 0;
    state.tab = 'knowledge';
    state.quizChoices = {};
    state.notesDirty = false;
    state.notesConflict = false;
    if (updateLocation) history.replaceState(null, '', '#chapter/' + encodeURIComponent(id));
    renderChapter();
    renderHistory({revealCurrent: true});
  } catch (error) { if (revision === state.loading) showLoadError(error.message, id); }
  finally {
    if (revision === state.loading) {
      $('#study-content').setAttribute('aria-busy', 'false');
      $('#navigation-status').hidden = true;
    }
  }
}

function renderChapter() {
  const chapter = state.chapter;
  $('#notes').disabled = false;
  $('#save-notes').disabled = false;
  $('#question').disabled = false;
  $('#ask-button').disabled = state.asking;
  $$('[data-question]').forEach(button => button.disabled = false);
  document.title = chapter.title + ' · 知章';
  $('#breadcrumb-course').textContent = chapter.course;
  $('#chapter-title').textContent = chapter.title;
  $('#chapter-book').textContent = chapter.textbook || '未指定教材';
  $('#chapter-eyebrow').textContent = chapter.completed ? '这一章，已经学完' : '今天，又懂了一点';
  $('#mode-badge').textContent = modeLabels[chapter.mode];
  $('#mode-badge').className = 'badge ' + chapter.mode;
  $('#demo-notice').hidden = chapter.mode !== 'demo';
  $('#topic-notice').hidden = chapter.mode !== 'topic';
  $('#overview-text').textContent = chapter.guide.overview;
  $('#prerequisites').innerHTML = chapter.guide.prerequisites.length ? `<strong>先备知识</strong>${chapter.guide.prerequisites.map(escapeHTML).join(' · ')}` : '<strong>从这里开始</strong>跟着知识点逐步理解，再用自测检查掌握情况。';
  $('#objectives').innerHTML = textList(chapter.guide.objectives);
  $('#knowledge-count').textContent = chapter.guide.knowledge_points.length;
  $('#notes').value = chapter.notes;
  $('#notes-status').textContent = chapter.notes ? '已保存到本机' : '';
  $('#merge-notes').hidden = true;
  $('#export-link').href = '/api/chapters/' + encodeURIComponent(chapter.id) + '/export';
  $('#mark-complete').disabled = pendingProgress.has(chapter.id);
  $('#mark-complete').textContent = chapter.completed ? '✓ 已学完 · 可撤回' : '标记已学完';
  renderProgress();
  renderMessages();
  switchTab(state.tab);
  window.dispatchEvent(new Event('chapter-rendered'));
}

function renderProgress() {
  const chapter = state.chapter;
  const answered = chapter.attempts.length;
  const correct = chapter.attempts.filter(attempt => attempt.correct).length;
  const percent = Math.round(answered / chapter.guide.quiz.length * 100);
  $('#progress-label').textContent = answered ? `已答 ${answered}/${chapter.guide.quiz.length} 题 · 答对 ${correct} 题` : '还未完成自测';
  $('#progress-number').textContent = percent + '%';
  $('#quiz-progress').value = percent;
}

function switchTab(tab, focus = false) {
  if (!state.chapter || !['knowledge', 'examples', 'analogies', 'quiz', 'sources'].includes(tab)) return;
  state.tab = tab;
  $$('.study-tabs [role=tab]').forEach(button => {
    button.setAttribute('aria-selected', String(button.dataset.tab === tab));
    button.tabIndex = button.dataset.tab === tab ? 0 : -1;
  });
  $('#study-content').setAttribute('aria-labelledby', 'tab-' + tab);
  renderContent();
  if (focus) $('#tab-' + tab).focus();
}

function renderContent() {
  const chapter = state.chapter, guide = chapter.guide;
  const content = $('#study-content');
  const focused = document.activeElement;
  const quizFocus = content.contains(focused) && focused?.matches('input[name^="quiz-"]') ? {name: focused.name, value: focused.value} : null;
  if (state.tab === 'knowledge') {
    content.innerHTML = `<p class="section-caption">先理解概念，再用自己的话说一遍。不确定的地方，可以直接追问。</p>` + guide.knowledge_points.map((point, index) => `<article class="knowledge-card"><div class="knowledge-top"><span class="number-label">${String(index + 1).padStart(2, '0')}</span><h3>${escapeHTML(point.title)}</h3></div><p class="explanation">${escapeHTML(point.explanation)}</p><div class="takeaway"><strong>记住这一句</strong>${escapeHTML(point.key_takeaway)}</div>${point.pitfalls.length ? `<ul class="pitfall-list">${textList(point.pitfalls)}</ul>` : ''}<div class="card-actions"><div class="source-links">${point.source_ids.map(sourceLink).join('') || '<span class="section-caption">补充讲解 · 无教材原文引用</span>'}</div><button class="text-button" data-point="${index}">这个知识点，再讲讲 ↗</button></div></article>`).join('');
  } else if (state.tab === 'examples') {
    content.innerHTML = '<p class="section-caption">以下为教学原创例题。先自己思考，再展开解题过程。</p>' + guide.worked_examples.map((example, index) => `<article class="example-card"><span class="tag">例题 ${String(index + 1).padStart(2, '0')}</span><h3>${escapeHTML(example.title)}</h3><div class="question-box"><p>${escapeHTML(example.question)}</p></div><details><summary>查看思路与解析</summary><ol class="steps">${textList(example.steps)}</ol><div class="answer-box"><strong>答案：</strong>${escapeHTML(example.answer)}</div>${example.mistake ? `<p class="mistake"><strong>易错提醒：</strong>${escapeHTML(example.mistake)}</p>` : ''}</details></article>`).join('');
  } else if (state.tab === 'analogies') {
    content.innerHTML = '<p class="section-caption">用熟悉的场景理解抽象知识，也记得留意类比的边界。</p>' + guide.analogies.map(analogy => `<article class="analogy-card"><span class="tag">换个角度理解</span><h3>${escapeHTML(analogy.concept)}</h3><p>${escapeHTML(analogy.scene)}</p><ul class="mapping">${textList(analogy.mapping)}</ul><div class="limitation"><strong>这个类比，哪里不完全一样？</strong>${escapeHTML(analogy.limitation)}</div></article>`).join('');
  } else if (state.tab === 'quiz') {
    const correct = chapter.attempts.filter(a => a.correct).length;
    content.innerHTML = `<div class="quiz-summary">${chapter.attempts.length ? `已完成 ${chapter.attempts.length}/${guide.quiz.length} 题，答对 ${correct} 题。可以修改选项后重新检查答案。` : '不用急着看答案。选好选项后，点击「检查答案」，看看自己真正理解了多少。'}</div>` + guide.quiz.map((question, index) => {
      const attempt = chapter.attempts.find(item => item.question_index === index);
      const pending = pendingQuiz.has(`${chapter.id}:${index}`);
      const selected = state.quizChoices[index] ?? attempt?.choice;
      const showFeedback = attempt && selected === attempt.choice;
      return `<article class="quiz-card" id="quiz-${index}"><span class="tag">自测 ${index + 1} / ${guide.quiz.length}</span><h3 id="quiz-title-${index}">${escapeHTML(question.question)}</h3><div class="quiz-options" role="radiogroup" aria-labelledby="quiz-title-${index}">${question.options.map((option, choice) => `<label class="quiz-option ${showFeedback && choice === question.answer ? 'correct' : ''} ${showFeedback && choice === attempt.choice && !attempt.correct ? 'incorrect' : ''}"><input type="radio" name="quiz-${index}" value="${choice}" ${selected === choice ? 'checked' : ''}><span>${String.fromCharCode(65 + choice)}. ${escapeHTML(option)}</span></label>`).join('')}</div><button class="button secondary small" data-check="${index}" ${pending ? 'disabled' : ''}>${pending ? '正在检查…' : (attempt ? '重新检查' : '检查答案')}</button>${showFeedback ? `<div class="quiz-feedback ${attempt.correct ? '' : 'wrong'}" role="status"><strong>${attempt.correct ? '答对了！' : '再想一想，正确答案是 ' + String.fromCharCode(65 + question.answer) + '。'}</strong><p>${escapeHTML(question.explanation)}</p></div>` : ''}</article>`;
    }).join('');
  } else {
    content.innerHTML = chapter.sources.length ? `<p class="section-caption">以下是参与整理的正文或原创参考说明，请结合来源类型核对。PDF 页码指文件页序；外部参考链接需要联网，讲义正文可离线阅读。</p>` + chapter.sources.map((source, index) => `<article class="source-card" id="source-${escapeHTML(source.id)}"><span class="tag">资料 ${index + 1}</span><h3>${escapeHTML(source.label)}</h3><pre>${sourceText(source.text)}</pre></article>`).join('') : '<div class="empty-state"><h3>这一章还没有教材原文</h3><p>当前内容是根据标题生成的通用讲解。上传教材正文后重新整理，即可查看实际使用的资料片段。</p></div>';
  }
  if (quizFocus) $$('input[name^="quiz-"]', content).find(input => input.name === quizFocus.name && input.value === quizFocus.value)?.focus({preventScroll: true});
}

function renderMessages({preserveScroll = false} = {}) {
  const messages = $('#messages');
  const oldHeight = messages.scrollHeight, oldTop = messages.scrollTop;
  const all = state.chapter.messages;
  const visible = all.slice(-messageLimit);
  const remaining = all.length - visible.length;
  const older = $('#older-messages');
  older.hidden = remaining === 0 && !state.chapter.messages_has_more;
  older.disabled = pendingMessagePages.has(state.chapter);
  older.textContent = older.disabled ? '正在加载更早的问答…' : state.chapter.messages_has_more ? '查看更早的问答' : `查看更早的 ${Math.min(MESSAGE_BATCH_SIZE, remaining)} 条问答（还有 ${remaining} 条）`;
  messages.innerHTML = visible.map(message => `<div class="message question"><span class="message-label">我想知道</span>${escapeHTML(message.question)}</div><div class="message answer"><span class="message-label">知章</span>${escapeHTML(message.answer).replace(/\[([a-zA-Z0-9-]+)\]/g, (match, id) => sourceLink(id))}</div>`).join('');
  messages.scrollTop = preserveScroll ? oldTop + messages.scrollHeight - oldHeight : messages.scrollHeight;
}

async function loadOlderMessages() {
  const chapter = state.chapter;
  if (!chapter || pendingMessagePages.has(chapter)) return;
  pendingMessagePages.add(chapter);
  renderMessages({preserveScroll: true});
  try {
    if (chapter.messages.length - messageLimit < MESSAGE_BATCH_SIZE && chapter.messages_has_more) {
      const page = await api(`/chapters/${encodeURIComponent(chapter.id)}/messages?before=${chapter.messages_cursor}`);
      if (state.chapter !== chapter) return;
      const messages = new Map(chapter.messages.map(message => [message.id, message]));
      page.messages.forEach(message => messages.set(message.id, message));
      chapter.messages = [...messages.values()].sort((a, b) => a.id - b.id);
      chapter.messages_has_more = page.messages_has_more;
      chapter.messages_cursor = page.messages_cursor;
    }
    if (state.chapter === chapter) messageLimit += MESSAGE_BATCH_SIZE;
  } catch (error) {
    if (state.chapter === chapter) showToast('更早的问答未加载，请重试。' + error.message, true);
  } finally {
    pendingMessagePages.delete(chapter);
    if (state.chapter === chapter) {
      renderMessages({preserveScroll: true});
      if ($('#older-messages').hidden) $('#messages').focus({preventScroll: true});
    }
  }
}

function syncAnswerButtons() {
  if (state.chapter && state.tab === 'quiz') {
    $$('[data-check]').forEach(button => {
      const index = Number(button.dataset.check);
      const pending = pendingQuiz.has(`${state.chapter.id}:${index}`);
      button.disabled = pending;
      button.textContent = pending ? '正在检查…' : (state.chapter.attempts.some(item => item.question_index === index) ? '重新检查' : '检查答案');
    });
  }
  $$('[data-review-check]').forEach(button => {
    const item = state.reviewItems[Number(button.dataset.reviewCheck)];
    button.disabled = state.reviewSaving || Boolean(item && pendingQuiz.has(`${item.chapter_id}:${item.question_index}`));
  });
}

async function checkAnswer(index, button) {
  const chapterId = state.chapter.id;
  const key = `${chapterId}:${index}`;
  if (pendingQuiz.has(key)) return;
  const selected = $(`input[name="quiz-${index}"]:checked`);
  if (!selected) { showToast('先选一个答案，再检查。'); return; }
  const hadFocus = document.activeElement === button;
  pendingQuiz.add(key);
  syncAnswerButtons();
  try {
    const choice = Number(selected.value);
    const result = await api(`/chapters/${encodeURIComponent(chapterId)}/attempts`, {method: 'POST', body: JSON.stringify({question_index: index, choice})});
    window.dispatchEvent(new CustomEvent('learning-answer', {detail: result}));
    if (state.chapter.id !== chapterId) return;
    state.chapter.attempts = state.chapter.attempts.filter(item => item.question_index !== index);
    state.chapter.attempts.push({question_index: index, choice, correct: result.correct});
    renderProgress();
    if (state.tab === 'quiz') {
      renderContent();
    }
    refreshHistory().catch(error => showToast(error.message, true));
  } catch (error) { showToast(error.message, true); }
  finally {
    pendingQuiz.delete(key);
    syncAnswerButtons();
    if (state.chapter?.id === chapterId && state.tab === 'quiz' && hadFocus && document.activeElement === document.body) {
      $(`[data-check="${index}"]`)?.focus({preventScroll: true});
    }
  }
}

// Drafts belong to a chapter for the lifetime of this page; they are not saved notes.
function rememberQuestion(edited = false) {
  if (!state.chapter) return;
  const previous = questionDrafts.get(state.chapter.id);
  const text = $('#question').value;
  const revision = (previous?.revision ?? 0) + (edited || text !== previous?.text ? 1 : 0);
  questionDrafts.set(state.chapter.id, {text, revision});
}

function setQuestion(question) {
  $('#question').value = question;
  rememberQuestion(true);
  $('#question').focus();
  $('#chat-form').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

async function askQuestion(event) {
  event.preventDefault();
  if (!state.chapter || state.asking) return;
  const question = $('#question').value.trim();
  if (!question) return;
  if (!state.settings?.configured) { showToast('先连接一个模型，再继续追问。'); await openSettings(); return; }
  const chapterId = state.chapter.id;
  rememberQuestion();
  const submittedRevision = questionDrafts.get(chapterId).revision;
  state.asking = true;
  $('#ask-button').disabled = true;
  $('#ask-button').textContent = '思考中…';
  $('#chat-hint').textContent = '正在结合本章内容回答';
  try {
    const result = await api(`/chapters/${encodeURIComponent(chapterId)}/questions`, {method: 'POST', body: JSON.stringify({question})});
    rememberQuestion();
    const draft = questionDrafts.get(chapterId);
    if (draft?.revision === submittedRevision) {
      questionDrafts.set(chapterId, {text: '', revision: submittedRevision + 1});
      if (state.chapter.id === chapterId) $('#question').value = '';
    }
    if (state.chapter.id === chapterId) {
      if (!state.chapter.messages.some(message => message.id === result.id)) state.chapter.messages.push(result);
      state.chapter.messages.sort((a, b) => a.id - b.id);
      renderMessages();
    } else showToast('上一章节的回答已保存，切换回去即可查看。');
  } catch (error) { showToast(error.message, true); }
  finally {
    state.asking = false;
    $('#ask-button').disabled = false;
    $('#ask-button').textContent = '提问 ↑';
    updateSettingsState();
  }
}

function updateSettingsState() {
  $('#settings-state').textContent = state.settings?.configured ? '已配置' : '未配置';
  $('#chat-hint').textContent = state.asking ? '正在结合提问时的章节回答' : (state.settings?.configured ? '结合当前章节回答' : '需要连接模型');
}

async function openSettings() {
  if (openingSettings || $('#settings-dialog').open) return;
  if (savingSettings) { showToast('模型设置正在保存，请稍等。'); return; }
  openingSettings = true;
  try {
    state.settings = await api('/settings');
    ++state.settingsEpoch;
    updateSettingsState();
    $('#base-url').value = state.settings.base_url;
    $('#model-name').value = state.settings.model;
    $('#api-key').value = '';
    $('#api-key').placeholder = state.settings.has_key ? '已保存密钥；留空则保留（更换服务地址时除外）' : '本机无密钥服务可以留空';
    const managed = state.settings.environment_managed;
    ['#base-url', '#model-name', '#api-key', '#save-settings'].forEach(id => $(id).disabled = managed);
    $('#key-help').textContent = managed ? '当前设置由启动环境变量提供，请修改环境变量后重启。' : '密钥保存在本机 data/settings.json 中，不会返回给网页。请勿分享 data 文件夹。';
    showMessage('#settings-error', state.settings.config_error || '');
    showMessage('#settings-success', '');
    if (!$('#settings-dialog').open) $('#settings-dialog').showModal();
  } catch (error) { showToast(error.message, true); }
  finally { openingSettings = false; }
}

async function saveSettings(event) {
  event.preventDefault();
  if (savingSettings) return;
  savingSettings = true;
  const revision = settingsEditRevision;
  $('#save-settings').disabled = true;
  showMessage('#settings-error', '');
  showMessage('#settings-success', '');
  const key = $('#api-key').value.trim();
  try {
    state.settings = await api('/settings', {method: 'PUT', body: JSON.stringify({base_url: $('#base-url').value.trim(), model: $('#model-name').value.trim(), api_key: key || null})});
    ++state.settingsEpoch;
    if ($('#api-key').value.trim() === key) $('#api-key').value = '';
    $('#api-key').placeholder = state.settings.has_key ? '已保存密钥，留空则保留' : '本机无密钥服务可以留空';
    updateSettingsState();
    showMessage('#settings-success', revision === settingsEditRevision ? '设置已保存。点击「测试已保存的连接」检查是否可用。' : '已保存提交时的设置；你刚修改的内容尚未保存，请再次点击保存。');
  } catch (error) { showMessage('#settings-error', error.message); }
  finally { savingSettings = false; $('#save-settings').disabled = false; }
}

async function testModel() {
  const button = $('#test-model');
  const epoch = state.settingsEpoch;
  button.disabled = true;
  button.textContent = '正在测试…';
  showMessage('#settings-error', '');
  showMessage('#settings-success', '');
  try { const result = await api('/settings/test', {method: 'POST'}); showMessage('#settings-success', epoch === state.settingsEpoch ? result.message : '设置已变化，请重新测试当前保存的连接。'); }
  catch (error) { showMessage('#settings-error', epoch === state.settingsEpoch ? error.message : '设置已变化，请重新测试当前保存的连接。'); }
  finally { button.disabled = false; button.textContent = '测试已保存的连接'; }
}

function getDraft() {
  return {course: $('#course-input').value, textbook: $('#book-input').value, chapter: $('#title-input').value, mode: $('#chapter-form [name=mode]:checked').value, text: $('#text-input').value, goal: $('#goal-input').value, level: $('#level-input').value, material: state.material, page_start: $('#page-start').value, page_end: $('#page-end').value, outline_index: $('#outline-select').value};
}

function saveDraft() {
  clearTimeout(draftTimer);
  draftTimer = null;
  try { localStorage.setItem('zhizhang-draft', JSON.stringify(getDraft())); }
  catch { /* Saving a draft is optional; authoritative learning records live on the backend. */ }
}

function scheduleDraft() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraft, 300);
}

function flushDraft() {
  if (draftTimer !== null) saveDraft();
}

function restoredMaterial(value) {
  if (!value || typeof value.id !== 'string' || !value.id || typeof value.name !== 'string'
      || !Number.isInteger(value.page_count) || value.page_count < 1 || value.page_count > 600
      || !Number.isFinite(value.characters) || value.characters < 0
      || typeof value.preview !== 'string' || !Array.isArray(value.warnings)
      || value.warnings.some(item => typeof item !== 'string')) return null;
  const outline = Array.isArray(value.outline) && value.outline.length <= 2000 ? value.outline.filter(item =>
    item && typeof item.title === 'string' && item.title.trim() && item.title.length <= 200
      && Number.isInteger(item.level) && item.level >= 0 && item.level <= 20
      && Number.isInteger(item.page_start) && item.page_start >= 1
      && Number.isInteger(item.page_end) && item.page_end >= item.page_start && item.page_end <= value.page_count
      && typeof item.range_inferred === 'boolean') : [];
  // An incomplete directory cannot justify inferred ranges; manual selection remains usable.
  const intact = Array.isArray(value.outline) && outline.length === value.outline.length;
  return {...value, outline: intact ? outline : []};
}

function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem('zhizhang-draft') || 'null');
    if (!draft) return;
    const fields = {course: '#course-input', textbook: '#book-input', chapter: '#title-input', text: '#text-input', goal: '#goal-input', level: '#level-input', page_start: '#page-start', page_end: '#page-end'};
    Object.entries(fields).forEach(([key, selector]) => { if (draft[key] != null) $(selector).value = draft[key]; });
    if (['material', 'topic'].includes(draft.mode)) $(`#chapter-form [name=mode][value=${draft.mode}]`).checked = true;
    const material = restoredMaterial(draft.material);
    if (material) {
      state.material = material;
      renderMaterial(false);
      if (state.material.outline?.[Number(draft.outline_index)]) $('#outline-select').value = String(draft.outline_index);
    }
  } catch { /* Ignore obsolete or malformed drafts. */ }
  finally { updateMode(); }
}

function updateMode() {
  const isMaterial = $('#chapter-form [name=mode]:checked').value === 'material';
  $('#material-fields').hidden = !isMaterial;
  $('#topic-help').hidden = isMaterial;
}

function renderMaterial(resetRange = true) {
  ++materialPreviewEpoch;
  $('#material-preview').hidden = !state.material;
  $('#outline-picker').hidden = !state.material?.outline?.length;
  showMessage('#outline-status', '');
  if (!state.material) return;
  const material = state.material;
  $('#outline-select').innerHTML = (material.outline || []).map((item, index) => `<option value="${index}">${'　'.repeat(Math.min(item.level, 4))}${escapeHTML(item.title)} · 文件第 ${item.page_start}${item.range_inferred && item.page_end !== item.page_start ? '–' + item.page_end : ''} 页${item.range_inferred ? '' : '（仅起始页）'}</option>`).join('');
  $('#material-name').textContent = material.name;
  $('#material-info').textContent = `共 ${material.page_count} 页，提取 ${material.characters.toLocaleString()} 字符。请用文件页序指定这一章的范围。`;
  $('#material-warnings').textContent = material.warnings.join('\n');
  $('#preview-text').textContent = material.preview || '本页未提取到文字。';
  $('#page-start').max = $('#page-end').max = $('#preview-page').max = material.page_count;
  if (resetRange) {
    $('#page-start').value = '1';
    $('#page-end').value = String(material.page_count);
    $('#preview-page').value = '1';
  }
}

function removeMaterial() {
  ++materialPreviewEpoch;
  ++materialUploadEpoch;
  materialUploadController?.abort();
  materialUploadController = null;
  if (state.uploading) {
    state.uploading = false;
    $('#generate-button').disabled = false;
    $('#file-input').disabled = false;
  }
  state.material = null;
  $('#file-input').value = '';
  $('#material-preview').hidden = true;
  $('#outline-picker').hidden = true;
  $('#upload-title').textContent = '选择教材或笔记文件';
  showMessage('#generation-error', '');
  saveDraft();
}

async function uploadFile() {
  const file = $('#file-input').files[0];
  if (!file || state.uploading) return;
  if (file.size > 20 * 1024 * 1024) { showMessage('#generation-error', '单个文件不能超过 20 MB，请拆分后上传。'); $('#file-input').value = ''; return; }
  state.uploading = true;
  const epoch = ++materialUploadEpoch;
  const controller = new AbortController();
  materialUploadController = controller;
  ++materialPreviewEpoch;
  $('#generate-button').disabled = true;
  $('#file-input').disabled = true;
  $('#upload-title').textContent = /\.(png|jpe?g|webp)$/i.test(file.name) ? '正在识别截图文字…' : '正在读取资料…';
  showMessage('#generation-error', '');
  const body = new FormData();
  body.append('file', file);
  try {
    const material = await api('/materials', {method: 'POST', body, signal: controller.signal});
    if (epoch !== materialUploadEpoch) return;
    state.material = material;
    renderMaterial();
    saveDraft();
    $('#upload-title').textContent = '资料已导入，可选择其他文件替换';
  } catch (error) {
    if (epoch !== materialUploadEpoch) return;
    showMessage('#generation-error', error.message + (state.material ? ' 上一份已导入的资料仍被保留，请确认下面的文件名。' : ''));
    $('#upload-title').textContent = '上传未完成，重新选择文件即可重试';
    $('#file-input').value = '';
  } finally {
    if (epoch === materialUploadEpoch) {
      materialUploadController = null;
      state.uploading = false;
      $('#generate-button').disabled = false;
      $('#file-input').disabled = false;
    }
  }
}

async function previewPage() {
  if (!state.material || state.uploading) return;
  const materialId = state.material.id;
  const page = Number($('#preview-page').value);
  const epoch = ++materialPreviewEpoch;
  showMessage('#generation-error', '');
  $('#preview-text').textContent = `正在读取文件第 ${page} 页…`;
  try {
    const result = await api(`/materials/${encodeURIComponent(materialId)}/pages/${page}`);
    if (epoch !== materialPreviewEpoch || state.material?.id !== materialId) return;
    $('#preview-text').textContent = result.text || '本页未提取到文字。';
  } catch (error) {
    if (epoch === materialPreviewEpoch) {
      $('#preview-text').textContent = '本页尚未加载，请检查页码后重试。';
      showMessage('#generation-error', error.message);
    }
  }
}

async function applyOutline() {
  if (state.uploading) return;
  const item = state.material?.outline?.[Number($('#outline-select').value)];
  if (!item) return;
  $('#title-input').value = item.title;
  $('#page-start').value = item.page_start;
  $('#page-end').value = item.page_end;
  $('#preview-page').value = item.page_start;
  $('#material-preview details').open = true;
  showMessage('#outline-status', item.range_inferred ? `已填入第 ${item.page_start}–${item.page_end} 页，请预览首尾页确认章节范围。` : '这个书签只能定位起始页，当前范围暂设为一页，请手动确认并调整结束页。');
  saveDraft();
  await previewPage();
}

async function editExtractedPage() {
  if (!state.material || state.uploading) return;
  const materialId = state.material.id;
  const epoch = ++materialPreviewEpoch;
  try {
    const page = Number($('#preview-page').value);
    const result = await api(`/materials/${encodeURIComponent(materialId)}/pages/${page}`);
    if (epoch !== materialPreviewEpoch || state.material?.id !== materialId) return;
    const combined = [$('#text-input').value.trim(), result.text].filter(Boolean).join('\n\n');
    if ([...combined].length > 90000) throw new Error('合并后的正文超过 9 万字。已保留现有正文和导入资料，请缩短正文后重试。');
    $('#text-input').value = combined;
    removeMaterial();
    $('#text-input').focus();
    saveDraft();
    showToast('本页文字已放入正文。导入文件已移除，避免重复引用；需要其他页时可重新上传。');
  } catch (error) {
    if (epoch === materialPreviewEpoch && state.material?.id === materialId) showMessage('#generation-error', error.message);
  }
}

function openNewChapter() {
  showMessage('#generation-error', '');
  $('#chapter-dialog').showModal();
}

async function generateChapter(event) {
  event.preventDefault();
  if (state.uploading) return;
  showMessage('#generation-error', '');
  if (!state.settings?.configured) {
    showMessage('#generation-error', '需要先配置模型服务。点击左下角「模型设置」，保存并测试连接后再来整理这一章。');
    return;
  }
  const draft = getDraft();
  if (draft.mode === 'material' && !state.material && draft.text.trim().length < 30) {
    showMessage('#generation-error', '请上传资料，或粘贴至少 30 字的章节正文。没有原文时，可以选择「按章节标题讲解」。');
    return;
  }
  const payload = {course: draft.course.trim(), textbook: draft.textbook.trim(), chapter: draft.chapter.trim(), mode: draft.mode, goal: draft.goal, level: draft.level, text: draft.mode === 'material' ? draft.text : ''};
  if (draft.mode === 'material' && state.material) {
    payload.material_id = state.material.id;
    payload.page_start = Number(draft.page_start);
    payload.page_end = Number(draft.page_end);
  }
  $('#generate-button').disabled = true;
  $('#generate-button').textContent = '正在提交…';
  saveDraft();
  try {
    const result = await api('/chapters/generate', {method: 'POST', body: JSON.stringify(payload)});
    state.job = result.job_id;
    try { localStorage.setItem('zhizhang-active-job', result.job_id); } catch {}
    $('#chapter-dialog').close();
    pollJob();
  } catch (error) { showMessage('#generation-error', error.message); }
  finally { $('#generate-button').disabled = false; $('#generate-button').textContent = '整理这一章 →'; }
}

async function openGeneratedChapter(chapterId) {
  const button = $('#open-generated-chapter');
  if (!button || button.dataset.chapter !== chapterId || button.disabled) return;
  const navigation = state.loading;
  button.disabled = true;
  try {
    await refreshHistory();
    if ($('#open-generated-chapter') !== button) return;
    if (state.loading === navigation) await openChapter(chapterId);
    if (state.chapter?.id === chapterId && $('#open-generated-chapter') === button) {
      $('#job-banner').hidden = true;
      showToast('这一章已整理好，开始学习吧。');
    }
  } catch (error) {
    if ($('#open-generated-chapter') === button) showToast(error.message, true);
  } finally { button.disabled = false; }
}

async function pollJob() {
  clearTimeout(state.polling);
  if (!state.job) return;
  const jobId = state.job;
  const banner = $('#job-banner');
  banner.hidden = false;
  try {
    const job = await api('/jobs/' + encodeURIComponent(jobId));
    if (state.job !== jobId) return;
    if (job.status === 'completed') {
      state.job = null;
      try { localStorage.removeItem('zhizhang-active-job'); } catch {}
      banner.classList.remove('failed');
      banner.innerHTML = `<strong>新章节已整理好。</strong> 暂时无法切换时，可以稍后打开。 <button class="text-button" id="open-generated-chapter" data-chapter="${escapeHTML(job.chapter_id)}">打开新章节</button><button class="text-button" id="dismiss-job">收起</button>`;
      await openGeneratedChapter(job.chapter_id);
      return;
    }
    if (job.status === 'failed') {
      banner.classList.add('failed');
      banner.innerHTML = `<strong>本次生成未完成。</strong> ${escapeHTML(job.error)} <button class="text-button" id="retry-generation">修改后重试</button><button class="text-button" id="dismiss-job">收起</button>`;
      state.job = null;
      try { localStorage.removeItem('zhizhang-active-job'); } catch {}
      return;
    }
    banner.classList.remove('failed');
    banner.innerHTML = `<span class="spinner" aria-hidden="true"></span>${escapeHTML(job.stage)}。你可以继续阅读已有章节，完成后会自动打开新内容。`;
  } catch (error) {
    banner.classList.add('failed');
    if (error.status === 404) {
      state.job = null;
      try { localStorage.removeItem('zhizhang-active-job'); } catch {}
      banner.innerHTML = '上次生成任务已不存在，可以重新整理章节。 <button class="text-button" id="retry-generation">重新整理</button><button class="text-button" id="dismiss-job">收起</button>';
      return;
    }
    banner.textContent = error.message + ' 正在尝试恢复任务状态…';
  }
  state.polling = setTimeout(pollJob, 2500);
}

function wireEvents() {
  $('#toggle-navigation').addEventListener('click', () => toggleNavigation($('#toggle-navigation').getAttribute('aria-expanded') !== 'true'));
  $('.sidebar').addEventListener('keydown', event => {
    if (event.key === 'Escape' && matchMedia('(max-width: 720px)').matches && $('#toggle-navigation').getAttribute('aria-expanded') === 'true') {
      toggleNavigation(false);
      $('#toggle-navigation').focus();
    }
  });
  $('#retry-load').addEventListener('click', async () => {
    $('#retry-load').disabled = true;
    try {
      if (failedChapterId) await openChapter(failedChapterId);
      else await loadStudy();
    } finally { $('#retry-load').disabled = false; }
  });
  $('#open-data').addEventListener('click', openData);
  $('#backup-export').addEventListener('click', exportBackup);
  $('#backup-file').addEventListener('change', previewBackup);
  $('#backup-import').addEventListener('click', importBackup);
  const filterHistory = () => { historyPage = 0; renderHistory(); $('#chapter-list').scrollTop = 0; $('#chapter-list').scrollLeft = 0; };
  $('#chapter-search').addEventListener('input', filterHistory);
  $('#chapter-filter').addEventListener('change', filterHistory);
  for (const [id, direction] of [['#history-prev', -1], ['#history-next', 1]]) {
    $(id).addEventListener('click', () => {
      historyPage += direction;
      renderHistory();
      $('#chapter-list').scrollTop = 0;
      $('#chapter-list').scrollLeft = 0;
      if ($(id).disabled) $(direction === 1 ? '#history-prev' : '#history-next').focus();
    });
  }
  $('#open-review').addEventListener('click', openReview);
  $('#refresh-review').addEventListener('click', openReview);
  for (const [id, direction] of [['#review-prev', -1], ['#review-next', 1]]) {
    $(id).addEventListener('click', () => {
      if (state.reviewSaving) return;
      reviewPage += direction;
      renderReview();
      $('#review-dialog').scrollTop = 0;
      $('#review-summary').focus({preventScroll: true});
    });
  }
  $('#review-list').addEventListener('change', event => {
    if (event.target.matches('input[type=radio][name^="review-"]')) {
      const index = Number(event.target.name.slice(7));
      state.reviewChoices[index] = Number(event.target.value);
      const card = event.target.closest('.review-card');
      $('.quiz-feedback', card)?.remove();
      $$('.quiz-option', card).forEach(option => option.classList.remove('correct', 'incorrect'));
    }
  });
  $('#review-list').addEventListener('click', async event => {
    const check = event.target.closest('[data-review-check]');
    if (check) { reviewAnswer(Number(check.dataset.reviewCheck)); return; }
    const open = event.target.closest('[data-review-open]');
    if (!open) return;
    if (state.reviewSaving) { showMessage('#review-error', '正在保存本次作答，请稍等。'); return; }
    const item = state.reviewItems[Number(open.dataset.reviewOpen)];
    if (!item) return;
    await openChapter(item.chapter_id);
    if (state.chapter?.id !== item.chapter_id) {
      showMessage('#review-error', '暂时无法切换章节。请先关闭本窗口，检查当前笔记是否保存成功。');
      return;
    }
    $('#review-dialog').close();
    switchTab('quiz');
    const question = $(`#quiz-${item.question_index}`);
    question.tabIndex = -1;
    question.focus({preventScroll: true});
    question.scrollIntoView({behavior: 'smooth', block: 'center'});
  });
  $('#chapter-list').addEventListener('click', async event => {
    if (event.target.closest('#clear-history-filters')) {
      $('#chapter-search').value = '';
      $('#chapter-filter').value = 'all';
      filterHistory();
      $('#chapter-search').focus();
      return;
    }
    const button = event.target.closest('[data-chapter]');
    if (button) {
      await openChapter(button.dataset.chapter);
      if (state.chapter?.id === button.dataset.chapter && matchMedia('(max-width: 720px)').matches) {
        toggleNavigation(false);
        $('#main').focus({preventScroll: true});
      }
    }
  });
  $('.study-tabs').addEventListener('click', event => {
    const button = event.target.closest('[data-tab]');
    if (button) switchTab(button.dataset.tab);
  });
  $('.study-tabs').addEventListener('keydown', event => {
    const buttons = $$('.study-tabs [role=tab]');
    let index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    if (event.key === 'ArrowRight') index = (index + 1) % buttons.length;
    else if (event.key === 'ArrowLeft') index = (index + buttons.length - 1) % buttons.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = buttons.length - 1;
    else return;
    event.preventDefault();
    switchTab(buttons[index].dataset.tab, true);
  });
  $('#study-content').addEventListener('click', event => {
    const sourceButton = event.target.closest('[data-source]');
    const pointButton = event.target.closest('[data-point]');
    const checkButton = event.target.closest('[data-check]');
    if (sourceButton) {
      showSource(sourceButton.dataset.source);
    }
    if (pointButton) setQuestion('关于「' + state.chapter.guide.knowledge_points[Number(pointButton.dataset.point)].title + '」，请换一种更简单的方式讲解，并举一个例子。');
    if (checkButton) checkAnswer(Number(checkButton.dataset.check), checkButton);
  });
  $('#messages').addEventListener('click', event => {
    const source = event.target.closest('[data-source]');
    if (source) showSource(source.dataset.source);
  });
  $('#older-messages').addEventListener('click', loadOlderMessages);
  $('#study-content').addEventListener('change', event => {
    if (event.target.matches('input[type=radio][name^="quiz-"]')) {
      const index = Number(event.target.name.slice(5));
      state.quizChoices[index] = Number(event.target.value);
      const key = `${state.chapter.id}:${index}`;
      quizChoiceRevisions.set(key, (quizChoiceRevisions.get(key) ?? 0) + 1);
      const card = event.target.closest('.quiz-card');
      $('.quiz-feedback', card)?.remove();
      $$('.quiz-option', card).forEach(option => option.classList.remove('correct', 'incorrect'));
    }
  });
  $$('[data-question]').forEach(button => button.addEventListener('click', () => setQuestion(button.dataset.question)));
  $$('[data-close]').forEach(button => button.addEventListener('click', () => $('#' + button.dataset.close).close()));
  $('#question').addEventListener('input', () => rememberQuestion(true));
  $('#chat-form').addEventListener('submit', askQuestion);
  $('#new-chapter').addEventListener('click', openNewChapter);
  $('#open-settings').addEventListener('click', openSettings);
  $('#configure-from-chapter').addEventListener('click', openSettings);
  $('#settings-form').addEventListener('submit', saveSettings);
  $('#settings-form').addEventListener('input', () => { ++settingsEditRevision; showMessage('#settings-success', ''); });
  $('#test-model').addEventListener('click', testModel);
  $('#chapter-form').addEventListener('submit', generateChapter);
  $('#chapter-form').addEventListener('input', scheduleDraft);
  $('#chapter-dialog').addEventListener('close', flushDraft);
  $$('#chapter-form [name=mode]').forEach(radio => radio.addEventListener('change', updateMode));
  $('#file-input').addEventListener('change', uploadFile);
  $('#remove-material').addEventListener('click', removeMaterial);
  $('#preview-load').addEventListener('click', previewPage);
  $('#apply-outline').addEventListener('click', applyOutline);
  $('#preview-page').addEventListener('input', () => {
    ++materialPreviewEpoch;
    $('#preview-text').textContent = '点击「查看」加载所选页的文字。';
  });
  $('#edit-extracted').addEventListener('click', editExtractedPage);
  $('#notes').addEventListener('input', () => {
    if (!state.chapter) return;
    state.notesDirty = true;
    $('#notes-status').textContent = '尚未保存';
    clearTimeout(state.notesTimer);
    state.notesTimer = setTimeout(() => saveNotes(true).catch(() => {}), 1000);
  });
  $('#save-notes').addEventListener('click', () => saveNotes().catch(() => {}));
  $('#merge-notes').addEventListener('click', async () => {
    if (!state.chapter) return;
    const chapterId = state.chapter.id;
    const button = $('#merge-notes');
    button.disabled = true;
    try {
      const latest = await api('/chapters/' + encodeURIComponent(chapterId));
      if (state.chapter.id !== chapterId) return;
      const local = $('#notes').value;
      $('#notes').value = latest.notes && !local.includes(latest.notes) ? local + '\n\n—— 另一窗口保存的笔记 ——\n' + latest.notes : local;
      if ($('#notes').value.length > 30000) {
        $('#notes').value = local;
        throw new Error('两份笔记合计超过 3 万字符。请先复制备份并缩短当前笔记，再合并。');
      }
      state.noteRevisions[chapterId] = latest.notes_revision ?? 0;
      state.notesConflict = false;
      state.notesDirty = true;
      button.hidden = true;
      await saveNotes();
    } catch (error) { showToast(error.message, true); }
    finally { button.disabled = false; }
  });
  $('#export-link').addEventListener('click', async event => {
    event.preventDefault();
    if (!state.chapter) return;
    const exportUrl = $('#export-link').href;
    try {
      while (state.notesDirty) await saveNotes(true);
      const download = document.createElement('a');
      download.href = exportUrl;
      download.download = '';
      document.body.append(download);
      download.click();
      download.remove();
    } catch (error) { showToast('笔记尚未保存，暂未导出。' + error.message, true); }
  });
  $('#print-chapter').addEventListener('click', async () => {
    if (!state.chapter) return;
    const chapterId = state.chapter.id;
    const handout = window.open('about:blank', '_blank');
    if (!handout) { showToast('请允许本地学习助手打开讲义窗口，再点击打印。', true); return; }
    handout.opener = null;
    handout.document.title = '正在准备学习讲义';
    handout.document.body.textContent = '正在保存笔记并准备学习讲义…';
    try {
      while (state.notesDirty) await saveNotes(true);
      if (!handout.closed) handout.location.replace('/api/chapters/' + encodeURIComponent(chapterId) + '/print');
    } catch (error) {
      handout.close();
      showToast('笔记尚未保存，暂未打开讲义。' + error.message, true);
    }
  });
  $('#mark-complete').addEventListener('click', async () => {
    if (!state.chapter) return;
    const button = $('#mark-complete'), chapterId = state.chapter.id;
    if (pendingProgress.has(chapterId)) return;
    pendingProgress.add(chapterId);
    button.disabled = true;
    try {
      const result = await api(`/chapters/${encodeURIComponent(chapterId)}/progress`, {method: 'PUT', body: JSON.stringify({completed: !state.chapter.completed})});
      if (state.chapter.id === chapterId) {
        state.chapter.completed = result.completed;
        button.textContent = result.completed ? '✓ 已学完 · 可撤回' : '标记已学完';
        $('#chapter-eyebrow').textContent = result.completed ? '这一章，已经学完' : '今天，又懂了一点';
      }
      await refreshHistory();
    } catch (error) { showToast(error.message, true); }
    finally {
      pendingProgress.delete(chapterId);
      button.disabled = pendingProgress.has(state.chapter?.id);
    }
  });
  $('#job-banner').addEventListener('click', event => {
    if (event.target.id === 'open-generated-chapter') openGeneratedChapter(event.target.dataset.chapter);
    if (event.target.id === 'retry-generation') openNewChapter();
    if (event.target.id === 'dismiss-job') $('#job-banner').hidden = true;
  });
  window.addEventListener('beforeunload', event => {
    flushDraft();
    if (state.notesDirty) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('pagehide', flushDraft);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushDraft(); });
  window.addEventListener('hashchange', () => {
    const id = chapterFromHash();
    if (id && id !== state.chapter?.id) openChapter(id, {updateLocation: false});
  });
}

function chapterFromHash() {
  try { return location.hash.startsWith('#chapter/') ? decodeURIComponent(location.hash.slice(9)) : null; }
  catch { return null; }
}

function registerWebMCP() {
  const context = document.modelContext;
  if (!context?.registerTool) return;
  const lifecycle = new AbortController();
  const tools = [
    {name: 'list_study_chapters', title: '查看学习章节', description: '读取本机保存的学习章节列表。', inputSchema: {type: 'object', properties: {}, additionalProperties: false}, annotations: {readOnlyHint: true, untrustedContentHint: true}, async execute() { await refreshHistory(); return state.chapters.map(({id, title, course, completed}) => ({id, title, course, completed})); }},
    {name: 'open_study_chapter', title: '打开学习章节', description: '在当前页面打开一个已保存的章节，不会生成新内容。', inputSchema: {type: 'object', properties: {chapter_id: {type: 'string'}}, required: ['chapter_id'], additionalProperties: false}, annotations: {readOnlyHint: false, untrustedContentHint: true}, async execute(input) {
      if (!input || typeof input.chapter_id !== 'string' || !state.chapters.some(chapter => chapter.id === input.chapter_id)) throw new Error('章节不存在');
      await openChapter(input.chapter_id);
      if (state.chapter?.id !== input.chapter_id) throw new Error('章节未能打开');
      return {id: state.chapter.id, title: state.chapter.title};
    }},
  ];
  tools.forEach(tool => { try { Promise.resolve(context.registerTool(tool, {signal: lifecycle.signal})).catch(() => {}); } catch {} });
  window.addEventListener('pagehide', () => lifecycle.abort(), {once: true});
}

async function loadStudy() {
  const epoch = ++studyLoadEpoch;
  showLoadError('');
  try {
    await refreshHistory();
    if (epoch !== studyLoadEpoch || state.chapter) return;
    let requested = chapterFromHash();
    if (!requested) {
      try { requested = localStorage.getItem('zhizhang-last-chapter'); } catch {}
    }
    const id = state.chapters.some(chapter => chapter.id === requested) ? requested : state.chapters[0]?.id;
    if (id) await openChapter(id);
  } catch (error) { if (epoch === studyLoadEpoch) showLoadError(error.message); }
}

async function initialize() {
  wireEvents();
  restoreDraft();
  const settingsEpoch = state.settingsEpoch;
  api('/settings').then(settings => {
    if (settingsEpoch !== state.settingsEpoch) return;
    state.settings = settings;
    updateSettingsState();
  }).catch(error => {
    if (settingsEpoch === state.settingsEpoch) showToast(error.message, true);
  });
  await loadStudy();
  try { state.job = localStorage.getItem('zhizhang-active-job'); } catch {}
  if (!state.job) {
    try { const jobs = await api('/jobs'); state.job = jobs.find(job => ['running', 'queued'].includes(job.status))?.id; } catch {}
  }
  if (state.job) pollJob();
  registerWebMCP();
}

initialize().catch(error => showLoadError(error.message));
