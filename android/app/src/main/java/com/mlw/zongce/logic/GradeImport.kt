package com.mlw.zongce.logic

/**
 * 抓取结果 → 课程行导入（对齐网页版 importGradeRows）：
 *  - 数字成绩 → 直接替换课程行参与加权（score 显示格式与 JS String(number) 对齐：整数不带 .0）；
 *  - 等级成绩（优秀/通过，无数值）→ 无法加权，由 UI 单独列出避免丢失；
 *  - 被剔除课程（体育等）→ 由 UI 单独提示。
 */
object GradeImport {

    /** JS String(88.0) === "88"：整数不带小数尾巴 */
    fun jsNumStr(d: Double): String =
        if (d == d.toLong().toDouble()) d.toLong().toString() else d.toString()

    /** 导入后的课程行（保证末尾有空行） */
    fun courseRows(outcome: CrawlOutcome): List<CourseEntry> {
        val numeric = outcome.rows.filter { it.score != null }
        val courses = numeric.map {
            CourseEntry(
                score = jsNumStr(it.score!!),
                credit = it.credit?.let(::jsNumStr) ?: "",
            )
        }.ifEmpty { listOf(CourseEntry()) }
        return if (courses.last().score.isNotEmpty() || courses.last().credit.isNotEmpty()) {
            courses + CourseEntry()
        } else courses
    }

    fun importInto(state: CalcState, outcome: CrawlOutcome): CalcState =
        state.copy(courses = courseRows(outcome))

    /** 数字成绩行数 */
    fun numericCount(outcome: CrawlOutcome): Int = outcome.rows.count { it.score != null }

    /** 等级成绩行数 */
    fun gradedCount(outcome: CrawlOutcome): Int = outcome.rows.count { it.score == null }

    /** 等级成绩提示行：名称（等级，X学分） */
    fun gradedDesc(r: GradeRow): String =
        "${r.name ?: "课程"}（${r.scoreRaw ?: "等级"}" +
            (r.credit?.let { "，${jsNumStr(it)}学分" } ?: "") + "）"

    /** 被剔除课程提示行：名称（score_raw，X学分） */
    fun excludedDesc(r: GradeRow): String =
        "${r.name ?: "课程"}（${r.scoreRaw ?: "-"}" +
            (r.credit?.let { "，${jsNumStr(it)}学分" } ?: "") + "）"
}
