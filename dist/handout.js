'use strict';

const printOptions = [
  ['include-solutions', 'hide-solutions', false],
  ['include-notes', 'hide-notes', false],
  ['include-sources', 'show-sources', true],
];
for (const [id, className, checkedMeansVisible] of printOptions) {
  const checkbox = document.getElementById(id);
  const apply = () => document.body.classList.toggle(className, checkedMeansVisible ? checkbox.checked : !checkbox.checked);
  checkbox.addEventListener('change', apply);
  apply();
}
document.getElementById('print-handout').addEventListener('click', () => {
  document.getElementById('print-status').textContent = '请在浏览器打印窗口确认页码和保存位置。';
  window.print();
});
