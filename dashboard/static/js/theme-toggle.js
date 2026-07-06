(function () {
  var root   = document.getElementById('root');
  var icon   = document.getElementById('toggle-icon');
  var label  = document.getElementById('toggle-label');
  var btn    = document.getElementById('theme-toggle');
  var saved  = localStorage.getItem('ds-theme') || 'dark';

  function apply(theme) {
    root.classList.remove('dark', 'light');
    root.classList.add(theme);
    icon.className = theme === 'dark' ? 'ti ti-moon' : 'ti ti-sun';
    label.textContent = theme === 'dark' ? 'Dark' : 'Light';
  }

  apply(saved);

  btn.addEventListener('click', function () {
    var next = root.classList.contains('dark') ? 'light' : 'dark';
    localStorage.setItem('ds-theme', next);
    apply(next);
  });
})();
