package com.mlw.zongce.logic

/**
 * 教务页面 HTML 解析（移植自 http_crawler.py 的标准库正则实现，不引入 HTML 解析库）。
 * 语义逐函数对齐：_attr / _input_value / _strip_tags / _parse_selects /
 * _find_grid_url / _is_semester_text / _is_semester_select / _is_year / _is_term /
 * _build_filters。
 */
object SelectParser {

    const val SEMESTER_ALL_VALUE = "001"
    const val GRID_API_KEY = "xsdQueryXscjList"
    const val ALL_VALUE = "__ALL__"

    data class SelectOption(val value: String, val text: String)

    data class SelectItem(
        val id: String,
        val name: String,
        val label: String,
        val options: List<SelectOption>,
    )

    data class FilterInfo(
        val year: SelectItem?,
        val term: SelectItem?,
        val semesterPair: Boolean,
        val allSelects: List<SelectItem>,
    )

    // ---- 基础工具 ---- //

    fun attr(attrs: String, name: String): String {
        val re = Regex("\\b" + Regex.escape(name) + "\\s*=\\s*([\"'])(.*?)\\1",
            setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
        return re.find(attrs)?.groupValues?.get(2) ?: ""
    }

    fun inputValue(html: String, fieldId: String): String {
        val re = Regex(
            "<input\\b[^>]*\\bid\\s*=\\s*[\"']" + Regex.escape(fieldId) + "[\"'][^>]*>",
            setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
        )
        val m = re.find(html) ?: return ""
        return attr(m.value, "value")
    }

    fun stripTags(s: String): String =
        Regex("<[^>]+>").replace(s, "").trim()

    // ---- 页面解析 ---- //

    private val SELECT_RE = Regex(
        "<select\\b([^>]*)>(.*?)</select>",
        setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
    )
    private val OPTION_RE = Regex(
        "<option\\b([^>]*)>(.*?)</option>",
        setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
    )

    fun parseSelects(html: String): List<SelectItem> {
        val items = mutableListOf<SelectItem>()
        for (sm in SELECT_RE.findAll(html)) {
            val attrs = sm.groupValues[1]
            val body = sm.groupValues[2]
            val opts = OPTION_RE.findAll(body).map { om ->
                SelectOption(attr(om.groupValues[1], "value"), stripTags(om.groupValues[2]))
            }.toList()
            val before = html.substring(0, sm.range.first)
            val label = stripTags(before.takeLast(160)).takeLast(80)
            items.add(
                SelectItem(
                    id = attr(attrs, "id"),
                    name = attr(attrs, "name"),
                    label = label,
                    options = opts,
                )
            )
        }
        return items
    }

    fun findGridUrl(html: String): String? {
        val patterns = listOf(
            Regex("url\\s*:\\s*[\"']([^\"']+)[\"']"),
            Regex("url\\s*:\\s*[A-Za-z_$][\\w$.]*\\s*\\+\\s*[\"']([^\"']+)[\"']"),
        )
        val found = mutableListOf<String>()
        for (p in patterns) found += p.findAll(html).flatMap { it.groupValues[1].let(::listOf) }.toList()
        return found.firstOrNull { it.contains(GRID_API_KEY) }
    }

    /** jqGrid 地址解析（对齐 http_crawler._resolve_grid_url）。
     *  "/\"开头按教务 BASE 根拼接——不是 passport 域；拼错域名会被教务网关 403。 */
    fun resolveGridUrl(contentUrl: String, gridPath: String, baseUrl: String): String = when {
        gridPath.startsWith("http") -> gridPath
        gridPath.startsWith("/") -> baseUrl + gridPath
        else -> {
            // 相对路径：以内容页所在目录为基准（保留 /admin//xsd 双斜杠形态）
            val idx = contentUrl.lastIndexOf('/')
            contentUrl.substring(0, idx + 1) + gridPath
        }
    }

    // ---- 筛选识别（与 GradeCrawler.discover_filters 判定一致） ---- //

    private val SEMESTER_TEXT_RE =
        Regex("^\\s*\\d{4}\\s*[-/]\\s*\\d{4}\\s*[-/]\\s*\\d+\\s*$")
    private val YEAR_TEXT_RE = Regex("\\d{4}\\s*[-/]\\s*\\d{4}")
    private val TERM_TEXT_RE =
        Regex("^\\s*([123]|[一二三四]|第?[一二三四123]学期?)\\s*$")

    fun isSemesterText(t: String): Boolean = SEMESTER_TEXT_RE.matches(t)

    fun isSemesterSelect(it: SelectItem): Boolean {
        if (it.options.isEmpty()) return false
        val hit = it.options.count { o -> isSemesterText(o.text) }
        return hit >= maxOf(1, it.options.size / 2)
    }

    fun isYear(it: SelectItem): Boolean {
        if ("学年" in it.label) return true
        return it.options.any { o -> YEAR_TEXT_RE.containsMatchIn(o.text) }
    }

    fun isTerm(it: SelectItem): Boolean {
        if ("学期" in it.label) return true
        val texts = it.options.map { o -> o.text }
        return texts.size >= 2 && texts.filter { it.isNotEmpty() }.all { TERM_TEXT_RE.matches(it) }
    }

    fun buildFilters(selects: List<SelectItem>, log: (String) -> Unit): FilterInfo {
        val semItems = selects.filter { isSemesterSelect(it) }
        val semesterPair = semItems.size >= 2
        var year: SelectItem?
        var term: SelectItem?
        if (semesterPair) {
            val first = semItems[0]
            val second = semItems.firstOrNull { it !== first } ?: semItems.last()
            var start = semItems.firstOrNull { "起始" in it.label } ?: first
            var end = semItems.firstOrNull { "终止" in it.label } ?: second
            if (end === start) {
                end = if (start === first) second else first
            }
            year = start
            term = end
        } else {
            year = selects.firstOrNull { isYear(it) }
            term = selects.firstOrNull { isTerm(it) && it !== year }
        }

        fun withAll(it: SelectItem?): SelectItem? {
            if (it == null) return null
            val hasAll = it.options.any { o ->
                "全部" in o.text || "入学以来" in o.text ||
                    o.value.isEmpty() || o.value == SEMESTER_ALL_VALUE
            }
            if (hasAll) return it
            return it.copy(options = listOf(SelectOption(ALL_VALUE, "全部")) + it.options)
        }

        log(
            "读取筛选条件：学年下拉 ${if (year != null) "有" else "无"}，学期下拉 ${if (term != null) "有" else "无"}" +
                (if (semesterPair) "（起止学年学期区间模式）" else "")
        )
        return FilterInfo(
            year = withAll(year),
            term = withAll(term),
            semesterPair = semesterPair,
            allSelects = selects,
        )
    }
}
