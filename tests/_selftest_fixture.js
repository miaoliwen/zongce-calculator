/* 虚构的示例数据（非真实成绩单）：课程/环节=[课程号]课程名，成绩在“综合成绩”，学分在“获得学分”
   （课程学分 xf 列为空 → 验证 credit 回退到 hdxf；个别行 zhcj 为空 → 验证回退到 fxcj） */
var ALL = [
  ["2025-2026-2", "CS1001", "程序设计基础", "82", 4],
  ["2025-2026-2", "CS1002", "数据库原理", "76", 4],
  ["2025-2026-2", "CS1003", "计算机网络", "88", 4],
  ["2025-2026-2", "GE2001", "思想道德与法治", "81", 3],
  ["2025-2026-2", "GE2002", "军事理论", "90", 1],
  ["2025-2026-2", "PE1001", "体育", "85.5", 2],
  ["2025-2026-2", "CS1004", "数据结构与算法", "78", 4],
  ["2025-2026-2", "GE2003", "大学英语", "83.5", 4],
  ["2025-2026-1", "MA1001", "高等数学", "74", 4],
  ["2025-2026-1", "CS1005", "操作系统", "69", 3],
  ["2025-2026-1", "CS1006", "Python程序设计", "91", 4],
  ["2025-2026-1", "GE2004", "艺术鉴赏", "95.5", 1]
].map(function (d) {
  return { xnxq: d[0], kcmc: "[" + d[1] + "]" + d[2], zhcj: d[3], hdxf: String(d[4]), xf: "", fxcj: "" };
});
/* 有一行只有“分项成绩”，没有“综合成绩” → 验证成绩回退 */
ALL[5].zhcj = "";
ALL[5].fxcj = "85.5";

var COLS = ["xnxq", "kcmc", "xf", "kcxz", "kcsx", "ksxs", "kclb", "fxcj", "zhcj", "jd", "hdxf", "tscjzwmc"];
var GID = "xsdcjcxGridIdGrid";
var state = { rows: [], page: 1 };

/* 每页条数选项：默认 20/50/500；用 #size=5,10 可切到小页（验证翻页拼接） */
var OPTS = (location.hash.match(/size=([\d,]+)/) || [])[1];
OPTS = OPTS ? OPTS.split(",") : ["20", "50", "500"];
var selbox = document.querySelector("select.ui-pg-selbox");
OPTS.forEach(function (v, i) {
  var o = document.createElement("option");
  o.value = v;
  o.textContent = v;
  if (i === 0) o.selected = true;
  selbox.appendChild(o);
});

function pageSize() {
  return parseInt(selbox.value, 10) || 20;
}

function render() {
  var size = pageSize();
  var total = state.rows.length;
  var pages = Math.max(1, Math.ceil(total / size));
  if (state.page > pages) state.page = pages;

  var start = (state.page - 1) * size;
  var tb = document.querySelector("#" + GID + " tbody");
  tb.innerHTML = "";
  state.rows.slice(start, start + size).forEach(function (r, i) {
    var tr = document.createElement("tr");
    tr.className = "jqgrow";
    tr.id = "row" + (start + i + 1);
    COLS.forEach(function (col) {
      var td = document.createElement("td");
      td.setAttribute("aria-describedby", GID + "_" + col);
      td.textContent = r[col] || "";
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });

  document.querySelector(".ui-jqgrid-pager input.ui-pg-input").value = String(state.page);
  document.querySelector(".ui-jqgrid-pager .pages").textContent = String(pages);
  document.querySelector(".ui-jqgrid-pager .records").textContent = String(total);

  document.getElementById("next_" + GID).className =
    "ui-pg-button" + (state.page >= pages ? " ui-state-disabled" : "");
  document.getElementById("prev_" + GID).className =
    "ui-pg-button" + (state.page <= 1 ? " ui-state-disabled" : "");
}

/* 真实页面按钮上的 onclick="search('<gridId>')" */
function search(gridId) {
  document.body.setAttribute("data-searched", "1");
  var s = document.getElementById("startXnxq").value;
  var e = document.getElementById("endXnxq").value;
  document.body.setAttribute("data-range", s + ".." + e);
  /* 真实页面：001 = 入学以来，等同不设边界 */
  var lo = (s === "001" || !s) ? "" : s;
  var hi = (e === "001" || !e) ? "9999" : e;
  if (lo > hi) { var tmp = lo; lo = hi; hi = tmp === "9999" ? "" : tmp; }
  setTimeout(function () {           /* 模拟 XHR 异步返回后渲染 */
    if (location.hash.indexOf("nofilter") >= 0) {
      /* 模拟“教务页面忽略了筛选条件”：无论选什么都返回全部学期 */
      state.rows = ALL.slice();
    } else {
      state.rows = ALL.filter(function (r) {
        return (!lo || r.xnxq >= lo) && (!hi || r.xnxq <= hi);
      });
    }
    state.page = 1;
    render();
  }, 300);
}

function setting(gridId) {
  document.body.setAttribute("data-setting", "1");
}

document.getElementById("next_" + GID).addEventListener("click", function () {
  var pages = Math.max(1, Math.ceil(state.rows.length / pageSize()));
  if (state.page < pages) {
    state.page += 1;
    render();
  }
});
document.getElementById("prev_" + GID).addEventListener("click", function () {
  if (state.page > 1) {
    state.page -= 1;
    render();
  }
});
selbox.addEventListener("change", function () {
  if (document.body.getAttribute("data-searched")) render();
});
document.getElementById("startXnxq").addEventListener("change", function () {
  document.body.setAttribute("data-start", this.value);
});
document.getElementById("endXnxq").addEventListener("change", function () {
  document.body.setAttribute("data-end", this.value);
});
render();