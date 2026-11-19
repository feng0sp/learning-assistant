'use strict';
(() => {
  let epoch = 0;
  let refreshTimer;
  let noticeTimer;
  function render(data) {
    $('#reward-pill-label').textContent = `Lv.${data.level} · ${data.points} 积分`;
    $('#growth-level').textContent = `Lv.${data.level} · ${data.title}`;
    $('#growth-points').textContent = `${data.points} 学习积分`;
    $('#growth-next').textContent = data.next_level ? `再积累 ${data.next_level - data.points} 分，走向下一阶段` : '所有成长等级已解锁，继续按自己的节奏复习吧';
    $('#growth-progress').max = data.next_level ? data.next_level - data.level_start : 1;
    $('#growth-progress').value = data.next_level ? data.points - data.level_start : 1;
    $('#growth-daily-count').textContent = `${Math.min(data.today, data.daily_goal)} / ${data.daily_goal}`;
    $('#growth-daily').textContent = data.today >= data.daily_goal ? `今日小目标已完成，已答对 ${data.today} 道不同课程题。` : `今天已答对 ${data.today} 道不同课程题，还差 ${data.daily_goal - data.today} 道。复习旧题也算。`;
    $('.daily-practice').classList.toggle('achieved', data.today >= data.daily_goal);
    const practice = data.next_practice;
    $('#practice-recommendation').textContent = practice ? `${practice.course_title || ''} · ${practice.reason}：${practice.title}` : '暂无待推荐的课程题，按自己的节奏休息或从目录选择复习吧。';
    $('#start-daily-practice').disabled = !practice || $('#start-daily-practice').dataset.opening === 'true';
    $('#start-daily-practice').dataset.practiceLesson = practice?.chapter_id || '';
    $('#start-daily-practice').dataset.practiceQuestion = practice?.question_index ?? '';
    $('#start-daily-practice').dataset.practiceCourse = practice?.course_id || 'network';
    $('#start-daily-practice').textContent = data.today >= data.daily_goal ? '继续巩固（可选）' : '开始今日练习';
    $('#growth-badges').innerHTML = data.badges.map(badge => `<article class="growth-badge ${badge.earned ? 'earned' : ''}"><span aria-hidden="true">${badge.earned ? '✦' : '◇'}</span><strong>${escapeHTML(badge.title)}</strong><small>${escapeHTML(badge.description)}</small><em>${badge.earned ? '已解锁' : '尚未解锁'}</em></article>`).join('');
  }
  async function refresh() {
    clearTimeout(refreshTimer);
    const revision = ++epoch;
    $('#rewards-content').setAttribute('aria-busy', 'true');
    try {
      const data = await api('/rewards');
      if (revision !== epoch) return;
      render(data);
      showMessage('#rewards-error', '');
      $('#rewards-retry').hidden = true;
    } catch (error) {
      if (revision === epoch) { showMessage('#rewards-error', error.message); $('#rewards-retry').hidden = false; }
    } finally { if (revision === epoch) $('#rewards-content').setAttribute('aria-busy', 'false'); }
  }
  $('#open-rewards').addEventListener('click', () => { $('#rewards-dialog').showModal(); refresh(); });
  $('#rewards-retry').addEventListener('click', refresh);
  $('#start-daily-practice').addEventListener('click', event => {
    const button = event.currentTarget;
    if (!button.dataset.practiceLesson || button.disabled) return;
    window.dispatchEvent(new CustomEvent('open-course-practice', {detail: {
      chapterId: button.dataset.practiceLesson, courseId: button.dataset.practiceCourse, questionIndex: Number(button.dataset.practiceQuestion),
    }}));
  });
  window.addEventListener('learning-data-imported', refresh);
  window.addEventListener('learning-answer', event => {
    scheduleRefresh();
    if (event.detail.reward_points > 0) {
      clearTimeout(noticeTimer);
      $('#reward-notice').textContent = `✦ 理解又进一步 · +${event.detail.reward_points} 学习积分`;
      $('#reward-notice').hidden = false;
      noticeTimer = setTimeout(() => { $('#reward-notice').hidden = true; }, 4000);
    }
  });
  function focusMode(enabled) {
    document.body.classList.toggle('focus-reading', enabled);
    $('#toggle-focus').setAttribute('aria-pressed', String(enabled));
    $('#toggle-focus').textContent = enabled ? '退出专注' : '专注阅读';
    try { sessionStorage.setItem('zhizhang-focus', String(enabled)); } catch {}
  }
  $('#toggle-focus').addEventListener('click', () => focusMode(!document.body.classList.contains('focus-reading')));
  try { focusMode(sessionStorage.getItem('zhizhang-focus') === 'true'); } catch {}
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, 120);
  }
  // Refresh on meaningful activity, including returning from another window.
  // Never block a chapter or poll in the background.
  window.addEventListener('chapter-rendered', scheduleRefresh);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') scheduleRefresh();
  });
})();
