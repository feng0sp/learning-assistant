'use strict';

(() => {
  let course = null;
  let selectedCourse = 'network';
  const catalogs = new Map();
  const requests = new Map();
  let opening = false;
  let labRevision = 0;
  let calculating = false;
  let catalogFrame;
  const courseOf = id => id?.startsWith('micro-v1-') ? 'microclass' : 'network';

  function courseBusy(busy) {
    opening = busy;
    $$('#course-dialog [data-network-lesson], #course-dialog [data-network-exam], #course-continue, #course-previous, #course-next').forEach(button => button.disabled = busy);
    if (!busy) { renderReadingNav(); renderContinue(); }
    $('#start-daily-practice').dataset.opening = String(busy);
    $('#start-daily-practice').disabled = busy || !$('#start-daily-practice').dataset.practiceLesson;
  }

  function renderContinue() {
    const button = $('#course-continue');
    const next = course?.lessons.find(item => !item.completed);
    button.disabled = opening || !next;
    button.dataset.networkLesson = next?.id || '';
    button.textContent = next ? (next.started ? '继续学习：' : '下一步：') + next.title : (course ? '全部课时已标记学完，可继续复习' : '正在读取课程…');
  }

  function renderCatalog() {
    if (!course) return;
    renderCourseHeader();
    const complete = course.lessons.filter(item => item.completed).length;
    const correct = course.lessons.reduce((sum, item) => sum + item.correct, 0);
    const attempted = course.lessons.reduce((sum, item) => sum + item.attempted, 0);
    const total = course.lessons.reduce((sum, item) => sum + item.questions, 0);
    $('#course-summary').textContent = `${complete} / ${course.lessons.length} 课已标记学完 · 已答 ${attempted} / ${total} 题 · 最近作答正确 ${correct} 题`;
    $('#course-progress').max = course.lessons.length;
    $('#course-progress').value = complete;
    renderContinue();
    const search = $('#course-search').value.trim().toLocaleLowerCase();
    const filter = $('#course-filter').value;
    const matches = course.lessons.filter(item => (!search || [item.title, ...item.topics].join(' ').toLocaleLowerCase().includes(search)) &&
      (filter === 'all' || filter === 'unfinished' && !item.completed || filter === 'started' && item.started || filter === 'completed' && item.completed || filter === 'wrong' && item.attempted > item.correct));
    $('#course-lessons').innerHTML = course.units.map((unit, index) => {
      const items = matches.filter(item => item.unit === index);
      if (!items.length) return '';
      const unitItems = course.lessons.filter(item => item.unit === index);
      const progress = `${unitItems.filter(item => item.completed).length}/${unitItems.length} 节已标学完 · 正确 ${unitItems.reduce((sum, item) => sum + item.correct, 0)}/${unitItems.reduce((sum, item) => sum + item.questions, 0)} 题`;
      return `<section class="course-unit"><h3><span>${String(index + 1).padStart(2, '0')}</span>${escapeHTML(unit)}</h3><p class="field-help">${progress}</p><div class="course-grid">${items.map(item => `<article class="course-lesson"><div class="course-lesson-heading"><span class="course-number">${String(item.number).padStart(2, '0')}</span><h4>${escapeHTML(item.title)}</h4></div><p>${item.topics.map(escapeHTML).join(' · ')}</p><div class="course-lesson-footer"><small>${item.completed ? '✓ 已标学完' : item.started ? '学习中' : '未开始'} · 正确 ${item.correct}/${item.questions}</small><button class="text-button" type="button" data-network-lesson="${escapeHTML(item.id)}" ${opening ? 'disabled' : ''}>${item.started ? '继续学习' : '开始学习'} →</button></div></article>`).join('')}</div></section>`;
    }).join('') || '<p class="dialog-intro">没有匹配的课时。可以更换关键词或选择“全部课时”。</p>';
  }

  function renderCourseHeader() {
    const micro = selectedCourse === 'microclass';
    $('#course-title').textContent = micro ? '微课堂前三章 · 学懂再练' : '计算机网络 · 完整学习路径';
    $('#course-intro').textContent = micro ? '概述、物理层、数据链路层 · 12 小节 · 120 道选择题与逐项解析。' : '24 课 · 72 道自测。按知识点 → 例题 → 类比 → 自测学习，离线可用。';
    $('#course-about-text').textContent = micro ? '配套原创解析，尚未逐节核对视频字幕。参考可读取的课程公开资料及协议标准，不是视频逐字稿。每小节含 5 个知识点、2 道分步例题、生活类比和 10 道选择题；约 35 分钟，可按自己的节奏学习。' : '按本科通用体系组织的原创精讲，不对应某本教材的固定页码。每课含知识点、例题、类比和 3 道自测。两套综合练习使用课内题目交错组卷，适合复习而非独立考试测评。';
    $('#microclass-source').hidden = !micro;
    $('#course-search').placeholder = micro ? '例如：CRC、编码、ARP' : '例如：子网、TCP、DNS';
    $$('[data-network-exam]').forEach(button => { button.hidden = micro; });
    $$('[data-course-catalog]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.courseCatalog === selectedCourse)));
  }

  function fetchCourse(id = selectedCourse) {
    if (requests.has(id)) return requests.get(id);
    const request = readCourse(id).finally(() => requests.delete(id));
    requests.set(id, request);
    return request;
  }

  async function readCourse(id) {
    if (id === selectedCourse) {
      $('#course-lessons').setAttribute('aria-busy', 'true');
      showMessage('#course-error', '');
      $('#course-retry').hidden = true;
    }
    try {
      const result = await api('/course/' + id);
      catalogs.set(id, result);
      if (selectedCourse === id) { course = result; renderCatalog(); }
      renderReadingNav();
    } catch (error) {
      if (selectedCourse === id) {
        showMessage('#course-error', error.message);
        $('#course-retry').hidden = false;
        if (!course) $('#course-summary').textContent = '课程暂时无法读取，请重试。';
      }
    } finally { if (selectedCourse === id) $('#course-lessons').setAttribute('aria-busy', 'false'); }
  }

  function openCourse(id = 'network') {
    if (selectedCourse !== id) {
      selectedCourse = id;
      $('#course-search').value = '';
      $('#course-filter').value = 'all';
    }
    course = catalogs.get(id) || null;
    renderCourseHeader();
    showMessage('#course-error', '');
    $('#course-retry').hidden = true;
    if (course) renderCatalog();
    else {
      $('#course-lessons').replaceChildren();
      $('#course-summary').textContent = '正在读取学习进度…';
      $('#course-progress').value = 0;
      renderContinue();
    }
    if (!$('#course-dialog').open) $('#course-dialog').showModal();
    $('#course-lessons').setAttribute('aria-busy', 'true');
    fetchCourse(id);
  }

  async function openCourseItem(path, exam = false, questionIndex = null) {
    if (opening) return;
    courseBusy(true);
    showMessage('#course-error', '');
    if (questionIndex !== null) showMessage('#rewards-error', '');
    const navigation = state.loading;
    try {
      const result = await api(path, {method: 'POST'});
      await refreshHistory();
      if (state.loading !== navigation) return;
      await openChapter(result.chapter_id);
      if (state.chapter?.id !== result.chapter_id) {
        const message = '暂未切换课时。请先处理当前笔记的保存问题，再重试。';
        showMessage('#course-error', message);
        if (questionIndex !== null) showMessage('#rewards-error', message);
        showToast(message, true);
        return;
      }
      $('#course-dialog').close();
      if (questionIndex !== null) $('#rewards-dialog').close();
      if (exam || questionIndex !== null) switchTab('quiz');
      toggleNavigation(false);
      $('#main').focus({preventScroll: true});
      $('#chapter-title').scrollIntoView({block: 'start'});
      if (questionIndex !== null) {
        const question = $(`#quiz-${questionIndex}`);
        if (question) {
          question.tabIndex = -1;
          question.focus({preventScroll: true});
          question.scrollIntoView({block: 'center'});
        }
      }
      const id = courseOf(result.chapter_id);
      if (requests.has(id)) await requests.get(id);
      await fetchCourse(id);
    } catch (error) {
      showMessage('#course-error', error.message);
      if (questionIndex !== null) showMessage('#rewards-error', error.message);
      showToast(error.message, true);
    }
    finally { courseBusy(false); }
  }

  function renderReadingNav() {
    const chapter = state.chapter;
    const readingCourse = catalogs.get(courseOf(chapter?.id));
    const index = readingCourse?.lessons.findIndex(item => item.id === chapter?.id) ?? -1;
    $('#course-reading-nav').hidden = index < 0;
    if (index >= 0) {
      $('#course-position').textContent = `${index + 1} / ${readingCourse.lessons.length} · ${readingCourse.units[readingCourse.lessons[index].unit]}`;
      for (const [id, offset] of [['#course-previous', -1], ['#course-next', 1]]) {
        const item = readingCourse.lessons[index + offset];
        $(id).disabled = opening || !item;
        $(id).dataset.networkLesson = item?.id || '';
      }
    }
    if (chapter?.id.startsWith('micro-v1-')) {
      $('#mode-badge').textContent = '微课堂配套原创';
      $('#demo-notice p').textContent = '配套原创解析，尚未逐节核对视频字幕。不是视频逐字稿；每小节的“资料来源”列有参考资料与原视频链接。讲解和自测离线可用。';
    } else if (chapter?.id.startsWith('net-v1-') || chapter?.id.startsWith('net-exam-v1-')) {
      $('#mode-badge').textContent = '内置原创课程';
      $('#demo-notice p').textContent = '这是知章编写的本科通用课程讲义与原创练习，非教材原文。可离线学习；需要对齐课本时，仍可通过“开始新章节”导入资料。';
    } else {
      $('#demo-notice p').textContent = '这是一份可直接体验的教学示例，非教材原文。点击「开始新章节」，整理你正在学的内容。';
    }
  }

  $('#open-course').addEventListener('click', () => openCourse('network'));
  $('#open-microclass').addEventListener('click', () => openCourse('microclass'));
  $$('[data-course-catalog]').forEach(button => button.addEventListener('click', () => openCourse(button.dataset.courseCatalog)));
  window.addEventListener('open-course-practice', event => {
    const {chapterId, questionIndex} = event.detail;
    if (typeof chapterId !== 'string' || !Number.isInteger(questionIndex) || questionIndex < 0) return;
    openCourseItem('/course/' + courseOf(chapterId) + '/lessons/' + encodeURIComponent(chapterId) + '/open', false, questionIndex);
  });
  $('#course-retry').addEventListener('click', () => fetchCourse());
  window.addEventListener('learning-data-imported', async () => {
    await Promise.allSettled([...catalogs.keys()].map(async id => {
      if (requests.has(id)) await requests.get(id);
      await fetchCourse(id);
    }));
  });
  $('#course-search').addEventListener('input', () => {
    cancelAnimationFrame(catalogFrame);
    catalogFrame = requestAnimationFrame(renderCatalog);
  });
  $('#course-filter').addEventListener('change', renderCatalog);
  document.addEventListener('click', event => {
    const lesson = event.target.closest('[data-network-lesson]');
    if (lesson?.dataset.networkLesson && !lesson.disabled) openCourseItem('/course/' + courseOf(lesson.dataset.networkLesson) + '/lessons/' + encodeURIComponent(lesson.dataset.networkLesson) + '/open');
    const exam = event.target.closest('[data-network-exam]');
    if (exam && !exam.disabled) openCourseItem('/course/network/exams/' + exam.dataset.networkExam + '/open', true);
  });
  window.addEventListener('chapter-rendered', () => {
    renderReadingNav();
    if ((state.chapter?.id.startsWith('net-v1-') || state.chapter?.id.startsWith('micro-v1-')) && !catalogs.has(courseOf(state.chapter.id))) fetchCourse(courseOf(state.chapter.id));
  });

  $('#open-network-labs').addEventListener('click', () => $('#network-labs-dialog').showModal());
  $('#network-lab-type').addEventListener('change', () => {
    const type = $('#network-lab-type').value;
    $$('[data-lab]').forEach(field => { field.hidden = field.dataset.lab !== type; field.disabled = field.hidden; });
  });
  $('#network-lab-form').addEventListener('input', () => {
    ++labRevision;
    $('#network-lab-result').hidden = true;
    showMessage('#network-lab-error', '');
  });
  $('#network-lab-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (calculating) return;
    calculating = true;
    const revision = labRevision;
    const type = $('#network-lab-type').value;
    const body = Object.fromEntries(new FormData(event.target));
    if (type === 'delay') Object.keys(body).forEach(key => body[key] = Number(body[key]));
    $('#network-lab-calculate').disabled = true;
    $('#network-lab-result').hidden = true;
    showMessage('#network-lab-error', '');
    try {
      const result = await api('/course/network/labs/' + type, {method: 'POST', body: JSON.stringify(body)});
      if (revision !== labRevision) return;
      $('#network-lab-result-title').textContent = result.title;
      $('#network-lab-steps').innerHTML = result.steps.map(step => `<li>${escapeHTML(step)}</li>`).join('');
      $('#network-lab-result').hidden = false;
    } catch (error) { if (revision === labRevision) showMessage('#network-lab-error', error.message); }
    finally { calculating = false; $('#network-lab-calculate').disabled = false; }
  });
})();
