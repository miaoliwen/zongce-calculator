package com.mlw.zongce.logic

import kotlinx.serialization.Serializable

/**
 * 综测计算 · 纯逻辑层（零 Android 依赖，可 JVM 单测）。
 * 与网页版 static/index.html 的 compute()/num()/has()/fmt() 逐语义对齐：
 *   academic = Σ(有效行 score×credit) / Σ(有效行 credit)   // 有效行：score 可解析且 credit > 0
 *   moral    = 思想品德考评分 × 0.8
 *   labor    = 宿舍平均分 × 0.8
 *   ability  = min(PU分 × 10, 100)
 *   body     = 体育总评 × 0.8 × 0.5 + 心理总评 × 0.8 × 0.5
 *   total    = 0.60×academic + 0.20×moral + 0.10×labor + 0.05×ability + 0.05×body
 * 满分 93.00（心理总评默认按 100 计）。
 */
@Serializable
data class CourseEntry(val score: String = "", val credit: String = "")

@Serializable
data class CalcState(
    val courses: List<CourseEntry> = List(INITIAL_BLANK_ROWS) { CourseEntry() },
    val moral: String = "",
    val labor: String = "",
    val pu: String = "",
    val sport: String = "",
    val psych: String = "100",
) {
    companion object {
        const val INITIAL_BLANK_ROWS = 5
    }
}

/** 可缩放的课程行字段 */
enum class CourseField { SCORE, CREDIT }

data class CalcResult(
    val academic: Double,
    val moral: Double,
    val labor: Double,
    val ability: Double,
    val body: Double,
    val total: Double,
    val sumWC: Double,
    val sumC: Double,
)

object CalcEngine {

    const val FULL_SCORE = 93.0

    /** 对齐 JS parseFloat：解析字符串开头的数字（"88abc" → 88.0），失败返回 NaN。 */
    private val LEADING_FLOAT =
        Regex("""[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?""")

    fun parseFloatJs(s: String?): Double {
        if (s == null) return Double.NaN
        val m = LEADING_FLOAT.find(s.trimStart()) ?: return Double.NaN
        return m.value.toDouble()
    }

    /** JS num()：parseFloat 失败按 0 计 */
    fun num(v: String): Double = parseFloatJs(v).let { if (it.isNaN()) 0.0 else it }

    /** JS has()：非空且 parseFloat 可解析 */
    fun has(v: String): Boolean = v.isNotEmpty() && !parseFloatJs(v).isNaN()

    fun compute(state: CalcState): CalcResult {
        var sumWC = 0.0
        var sumC = 0.0
        for (c in state.courses) {
            if (has(c.score) && num(c.credit) > 0) {
                sumWC += num(c.score) * num(c.credit)
                sumC += num(c.credit)
            }
        }
        val academic = if (sumC > 0) sumWC / sumC else 0.0
        val moral = num(state.moral) * 0.8
        val labor = num(state.labor) * 0.8
        val ability = minOf(num(state.pu) * 10, 100.0)
        val body = num(state.sport) * 0.8 * 0.5 + num(state.psych) * 0.8 * 0.5
        val total = 0.60 * academic + 0.20 * moral + 0.10 * labor +
            0.05 * ability + 0.05 * body
        return CalcResult(academic, moral, labor, ability, body, total, sumWC, sumC)
    }

    /** 对齐 JS toFixed(2) 的显示（Locale.US 保证小数点） */
    fun format(v: Double): String = String.format(java.util.Locale.US, "%.2f", v)
}

/**
 * 状态变更的纯函数集合（网页版对应事件处理逻辑），ViewModel 只做转发。
 */
object CalcActions {

    /**
     * 输入课程行。对齐网页版：改到最后一行且该行已有内容时，自动补一行空白行，
     * 保证末尾始终有可输入行。
     */
    fun inputCourse(state: CalcState, index: Int, field: CourseField, text: String): CalcState {
        if (index < 0 || index >= state.courses.size) return state
        val courses = state.courses.toMutableList()
        val row = courses[index]
        courses[index] = when (field) {
            CourseField.SCORE -> row.copy(score = text)
            CourseField.CREDIT -> row.copy(credit = text)
        }
        var next = state.copy(courses = courses)
        if (index == courses.lastIndex) {
            val last = courses.last()
            if (last.score.isNotEmpty() || last.credit.isNotEmpty()) {
                next = next.copy(courses = courses + CourseEntry())
            }
        }
        return next
    }

    /** 删除课程行；删除后保证末尾仍有空行（列表为空时补一行，避免无可输入行）。 */
    fun deleteCourse(state: CalcState, index: Int): CalcState {
        if (index < 0 || index >= state.courses.size) return state
        val courses = state.courses.toMutableList().apply { removeAt(index) }
        if (courses.isEmpty() || courses.last().score.isNotEmpty() || courses.last().credit.isNotEmpty()) {
            courses.add(CourseEntry())
        }
        return state.copy(courses = courses)
    }

    fun addCourse(state: CalcState): CalcState =
        state.copy(courses = state.courses + CourseEntry())

    /** 五个手填项：moral / labor / pu / sport / psych */
    fun setField(state: CalcState, key: String, text: String): CalcState = when (key) {
        "moral" -> state.copy(moral = text)
        "labor" -> state.copy(labor = text)
        "pu" -> state.copy(pu = text)
        "sport" -> state.copy(sport = text)
        "psych" -> state.copy(psych = text)
        else -> state
    }

    /** 全部清空：回到 5 个空白行、心理总评默认 100（对齐网页版 resetBtn）。 */
    fun reset(): CalcState = CalcState()
}
