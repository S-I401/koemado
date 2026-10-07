/* 会話画面の右上に、設定と「状態・ログ」（管理画面）へのリンクを出す。 */
(function () {
  var box = document.createElement("div");
  box.style.cssText = "position:fixed;top:8px;right:12px;z-index:50;font:13px 'Yu Gothic UI',Meiryo,sans-serif;background:rgba(255,255,255,.85);padding:3px 10px;border-radius:999px;box-shadow:0 1px 4px rgba(0,0,0,.2)";
  function link(href, text) {
    var a = document.createElement("a");
    a.href = href; a.textContent = text; a.style.cssText = "margin:0 6px;color:#2d5bd0;text-decoration:none";
    box.appendChild(a); return a;
  }
  link("/settings", "設定");
  fetch("/settings/api/links").then(function (r) { return r.json(); }).then(function (d) {
    if (d.console) link(d.console, "状態・ログ");
  }).catch(function () {});
  document.body.appendChild(box);
})();
