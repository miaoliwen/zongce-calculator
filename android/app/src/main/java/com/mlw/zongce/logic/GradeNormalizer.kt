package com.mlw.zongce.logic

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive

/**
 * 成绩归一化 / 合并去重 / 学期过滤 / 课程剔除。
 * 逐函数移植自 crawler.py GradeCrawler 的纯逻辑实现：
 * _map_field / _to_float / _split_code_name / _split_year_term / _normalize_row /
 * _key / _merge / _collect_rows / _semester_values / _has_all_option / _semester_range /
 * _row_semester / _filter_semesters / _exclude_courses。
 */

data class GradeRow(
    val code: String?,
    val name: String?,
    val credit: Double?,
    val scoreRaw: String?,
    val score: Double?,
    val nature: String?,
    val gpa: Double?,
    val teacher: String?,
    val year: String?,
    val term: String?,
    val classname: String?,
    val sources: List<String>,
)

/** 抓取结果：纳入专业成绩的行 + 各类被剔除项 */
data class CrawlOutcome(
    val rows: List<GradeRow>,
    val excluded: List<GradeRow>,
    val droppedBySemester: List<GradeRow>,
)

object GradeNormalizer {

    /** 「体育」不纳入专业成绩加权（综测规则），与 crawler.py EXCLUDE_COURSE_KEYWORDS 一致 */
    val EXCLUDE_COURSE_KEYWORDS = listOf("体育")
    const val SEMESTER_ALL_VALUE = SelectParser.SEMESTER_ALL_VALUE
    const val ALL_VALUE = SelectParser.ALL_VALUE

    // ---------------- 字段映射 ---------------- //

    /** 中英文接口键名 → 归一化字段（crawler.py FIELD_ALIASES 原样移植） */
    private val FIELD_ALIASES: Map<String, List<String>> = mapOf(
        "code" to listOf("kch", "kcdm", "kcbh", "课程号", "课程代码", "课程编号", "coursecode", "courseid"),
        "name" to listOf("kcmc", "课程名称", "课程名", "课程/环节", "课程环节", "coursename", "name", "kc"),
        "credit" to listOf("xf", "kcxf", "zxf", "学分", "课程学分", "credit", "coursecredit", "totalperiod"),
        "credit_earned" to listOf("hdxf", "hdxxf", "获得学分", "已获学分"),
        "score" to listOf("cj", "zcj", "zhcj", "zpcj", "成绩", "综合成绩", "总评成绩", "分数", "score", "grade", "cjms"),
        "score_item" to listOf("fxcj", "bfcj", "分项成绩"),
        "nature" to listOf("kcxz", "kclb", "kcsx", "课程性质", "课程属性", "课程类别", "nature", "type"),
        "gpa" to listOf("jd", "gpa", "绩点", "point"),
        "teacher" to listOf("jsmc", "js", "教师", "任课教师", "teacher"),
        "year" to listOf("xn", "xnxq", "学年学期", "学年", "schoolyear", "academicyear"),
        "term" to listOf("xq", "xqm", "学期", "term", "semester"),
        "classname" to listOf("jxbmc", "教学班", "班级"),
        "retake" to listOf("cxbs", "cxbj", "重修", "补考"),
    )

    private fun normKey(k: String): String =
        Regex("[\\s_\\-]").replace(k, "").lowercase()

    fun mapField(key: String): String? {
        val nk = normKey(key)
        for ((canon, aliases) in FIELD_ALIASES) {
            if (aliases.any { normKey(it) == nk }) return canon
        }
        return null
    }

    /** 从 '3.5学分'、'85'、'优秀' 之类中取浮点数，取不到返回 null */
    fun toFloat(s: Any?): Double? {
        if (s == null) return null
        val text = s.toString().replace(",", "")
        val m = Regex("-?\\d+(?:\\.\\d+)?").find(text) ?: return null
        return m.value.toDoubleOrNull()
    }

    // ---------------- 拆分工具 ---------------- //

    private val SPLIT_CODE_NAME_RE =
        Regex("^\\s*[\\[【]\\s*([A-Za-z0-9\\-_.]+)\\s*[\\]】]\\s*(.+)$")

    /** “[CS1001]程序设计基础” → ("CS1001", "程序设计基础")；拆不开时 code 为 null */
    fun splitCodeName(v: String?): Pair<String?, String> {
        val s = (v ?: "").trim()
        val m = SPLIT_CODE_NAME_RE.matchEntire(s) ?: return null to s
        return m.groupValues[1] to m.groupValues[2].trim()
    }

    private val SPLIT_YEAR_TERM_RE =
        Regex("^\\s*(\\d{4}\\s*[-/]\\s*\\d{4})\\s*[-/_]\\s*(\\d+)\\s*$")

    /** “2025-2026-2” → ("2025-2026", "2")；不是合并值则返回 (null, null) */
    fun splitYearTerm(v: String?): Pair<String?, String?> {
        val s = (v ?: "").trim()
        val m = SPLIT_YEAR_TERM_RE.matchEntire(s) ?: return null to null
        return Regex("\\s+").replace(m.groupValues[1], "") to m.groupValues[2]
    }

    // ---------------- 原始行收集（jqGrid JSON） ---------------- //

    /**
     * 递归遍历 JSON，把含已知字段的对象映射成原始行（crawler.py _collect_rows）。
     * 命中条件与 Python 一致：name/code 为真值，且 score/credit 字段存在且非 null。
     */
    fun collectRows(obj: JsonElement, out: MutableList<Map<String, String?>>) {
        when (obj) {
            is JsonObject -> {
                val mapped = LinkedHashMap<String, String?>()
                for ((k, v) in obj) {
                    val canon = mapField(k)
                    if (canon != null && v !is JsonObject && v !is JsonArray) {
                        mapped[canon] = if (v is JsonNull) null else v.jsonPrimitive.content
                    }
                }
                val nameTruthy = !mapped["name"].isNullOrEmpty()
                val codeTruthy = !mapped["code"].isNullOrEmpty()
                val scorePresent = mapped["score"] != null
                val creditPresent = mapped["credit"] != null
                if ((nameTruthy || codeTruthy) && (scorePresent || creditPresent)) {
                    out.add(mapped + ("_src" to "xhr"))
                }
                for (v in obj.values) collectRows(v, out)
            }
            is JsonArray -> for (v in obj) collectRows(v, out)
            else -> Unit
        }
    }

    // ---------------- 归一化与合并 ---------------- //

    fun normalizeRow(raw: Map<String, String?>): GradeRow {
        var score: String? = raw["score"]
        if (score.isNullOrEmpty()) score = raw["score_item"]
        var credit: String? = raw["credit"]
        if (credit.isNullOrEmpty()) credit = raw["credit_earned"]

        var code = (raw["code"] ?: "").trim().ifEmpty { null }
        var name = (raw["name"] ?: "").trim().ifEmpty { null }
        if (name != null && name.first() in "[【") {
            val (c2, n2) = splitCodeName(name)
            if (c2 != null) {
                if (code == null) code = c2
                name = n2
            }
        }
        if (name == null && code != null) name = code

        var year = (raw["year"] ?: "").trim().ifEmpty { null }
        var term = (raw["term"] ?: "").trim().ifEmpty { null }
        var (y2, t2) = splitYearTerm(raw["year"] ?: "")
        if (y2 == null) {
            val p = splitYearTerm(raw["term"] ?: "")
            y2 = p.first; t2 = p.second
        }
        if (y2 != null) {
            year = y2; term = t2
        }

        val src = raw["_src"] ?: "?"
        return GradeRow(
            code = code,
            name = name,
            credit = toFloat(credit),
            scoreRaw = score?.trim()?.ifEmpty { null },
            score = toFloat(score),
            nature = (raw["nature"] ?: "").trim().ifEmpty { null },
            gpa = toFloat(raw["gpa"]),
            teacher = (raw["teacher"] ?: "").trim().ifEmpty { null },
            year = year,
            term = term,
            classname = (raw["classname"] ?: "").trim().ifEmpty { null },
            sources = listOf(src),
        )
    }

    private data class MergeKey(val code: String, val name: String, val year: String, val termDigits: String)

    private fun mergeKey(r: GradeRow): MergeKey {
        val term = r.term ?: ""
        val digits = Regex("\\D").replace(term, "")
        return MergeKey(r.code ?: "", r.name ?: "", r.year ?: "", digits.ifEmpty { term })
    }

    /** 按「课程号/课程名 + 学年 + 学期」去重、互补缺失字段（crawler.py _merge） */
    fun merge(rawRows: List<Map<String, String?>>): List<GradeRow> {
        val merged = LinkedHashMap<MergeKey, GradeRow>()
        for (r in rawRows) {
            val n = normalizeRow(r)
            val k = mergeKey(n)
            val old = merged[k]
            if (old == null) {
                merged[k] = n
            } else {
                merged[k] = old.copy(
                    code = old.code ?: n.code,
                    name = old.name ?: n.name,
                    credit = old.credit ?: n.credit,
                    scoreRaw = old.scoreRaw ?: n.scoreRaw,
                    score = old.score ?: n.score,
                    nature = old.nature ?: n.nature,
                    gpa = old.gpa ?: n.gpa,
                    teacher = old.teacher ?: n.teacher,
                    year = old.year ?: n.year,
                    term = old.term ?: n.term,
                    classname = old.classname ?: n.classname,
                    sources = (old.sources + n.sources).distinct().sorted(),
                )
            }
        }
        return merged.values.toList()
    }

    // ---------------- 学期区间 ---------------- //

    private val SEMESTER_VALUE_RE =
        Regex("^\\s*\\d{4}\\s*[-/]\\s*\\d{4}\\s*[-/]\\s*\\d+\\s*$")

    /** 取“起始/终止学年学期”下拉里形如 2025-2026-2 的真实选项值 */
    fun semesterValues(item: SelectParser.SelectItem?): List<String> {
        if (item == null) return emptyList()
        return item.options
            .filter { SEMESTER_VALUE_RE.matches(it.text) }
            .map { it.value }
    }

    /** 页面是否自带“入学以来 / 全部”选项（value=001 或文本含入学以来/全部） */
    fun hasAllOption(item: SelectParser.SelectItem?): Boolean {
        if (item == null) return false
        return item.options.any { o ->
            o.value == SEMESTER_ALL_VALUE || "入学以来" in o.text || "全部" in o.text
        }
    }

    /**
     * 起止学年学期区间下拉的取值解析（crawler.py _semester_range）：
     * 两端都选具体学期 → 取小者为起始、大者为终止；只选一个 → 起止相同；
     * 选“全部” → 页面自带的“入学以来”(001)，没有则最早..最晚。
     */
    fun semesterRange(
        yearItem: SelectParser.SelectItem?,
        termItem: SelectParser.SelectItem?,
        yearValue: String?,
        termValue: String?,
    ): Pair<String, String> {
        val vals = (semesterValues(yearItem) + semesterValues(termItem)).distinct().sorted()

        fun specific(v: String?): String? =
            if (v != null && v != ALL_VALUE && v != SEMESTER_ALL_VALUE && v in vals) v else null

        val sv = specific(yearValue)
        val tv = specific(termValue)
        if (sv != null && tv != null) return if (sv <= tv) sv to tv else tv to sv
        if (sv != null || tv != null) {
            val one = sv ?: tv!!
            return one to one
        }
        if (hasAllOption(yearItem) || hasAllOption(termItem)) {
            return SEMESTER_ALL_VALUE to SEMESTER_ALL_VALUE
        }
        if (vals.isNotEmpty()) return vals.first() to vals.last()
        return ALL_VALUE to ALL_VALUE
    }

    // ---------------- 本机二次过滤与剔除 ---------------- //

    private val YEAR_ONLY_RE = Regex("^\\d{4}-\\d{4}-\\d+$")

    /** 把规范化行还原成页面里的学年学期取值，如 2025-2026-2 */
    fun rowSemester(r: GradeRow): String {
        val y = r.year
        val t = r.term
        if (!y.isNullOrEmpty() && !t.isNullOrEmpty()) return "$y-$t"
        if (!y.isNullOrEmpty() && YEAR_ONLY_RE.matches(y)) return y
        return ""
    }

    /**
     * 按所选学年学期在本机再过滤一次（教务页面筛选偶尔不生效的硬校验）。
     * 返回 (保留行, 剔除行)。
     */
    fun filterSemesters(rows: List<GradeRow>, startV: String, endV: String): Pair<List<GradeRow>, List<GradeRow>> {
        if (startV.isEmpty() || startV == SEMESTER_ALL_VALUE) return rows to emptyList()
        val (lo, hi) = if (startV <= endV) startV to endV else endV to startV
        val keep = mutableListOf<GradeRow>()
        val drop = mutableListOf<GradeRow>()
        for (r in rows) {
            val s = rowSemester(r)
            if (s.isEmpty()) drop.add(r)
            else if (s >= lo && s <= hi) keep.add(r)
            else drop.add(r)
        }
        return keep to drop
    }

    /** 剔除不纳入专业成绩的课程（默认：体育）。返回 (保留, 剔除) */
    fun excludeCourses(rows: List<GradeRow>): Pair<List<GradeRow>, List<GradeRow>> {
        val keep = mutableListOf<GradeRow>()
        val dropped = mutableListOf<GradeRow>()
        for (r in rows) {
            val name = r.name ?: ""
            val hit = EXCLUDE_COURSE_KEYWORDS.firstOrNull { it.isNotEmpty() && it in name }
            if (hit != null) dropped.add(r) else keep.add(r)
        }
        return keep to dropped
    }
}
