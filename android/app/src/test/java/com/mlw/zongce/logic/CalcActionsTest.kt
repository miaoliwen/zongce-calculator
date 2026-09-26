package com.mlw.zongce.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** 行为对齐网页版：末行输入自动补行、删除后保证末尾空行、清空回到初始态。 */
class CalcActionsTest {

    @Test
    fun `初始状态5个空行且心理默认100`() {
        val s = CalcState()
        assertEquals(5, s.courses.size)
        assertTrue(s.courses.all { it.score.isEmpty() && it.credit.isEmpty() })
        assertEquals("100", s.psych)
    }

    @Test
    fun `最后一行输入后自动补空白行`() {
        val s = CalcState().let { CalcActions.inputCourse(it, 4, CourseField.SCORE, "88") }
        assertEquals(6, s.courses.size)
        assertEquals("88", s.courses[4].score)
        assertTrue(s.courses[5].score.isEmpty() && s.courses[5].credit.isEmpty())
    }

    @Test
    fun `非最后一行输入不补行`() {
        val s = CalcState().let { CalcActions.inputCourse(it, 0, CourseField.SCORE, "88") }
        assertEquals(5, s.courses.size)
    }

    @Test
    fun `最后一行清空后不再补行`() {
        // 先造成 6 行（末行已填），再把末行内容清掉 → 不应再补
        var s = CalcState().let { CalcActions.inputCourse(it, 4, CourseField.SCORE, "88") }
        s = CalcActions.inputCourse(s, 4, CourseField.SCORE, "")
        assertEquals(6, s.courses.size)
    }

    @Test
    fun `删除后末尾保留空行`() {
        var s = CalcState().let { CalcActions.inputCourse(it, 4, CourseField.CREDIT, "3") }
        // 删除中间行，此时末行仍是刚填过的行（非空）→ 应补一个空行
        s = CalcActions.deleteCourse(s, 2)
        assertEquals(5, s.courses.size)
        assertTrue(s.courses.last().score.isEmpty() && s.courses.last().credit.isEmpty())
    }

    @Test
    fun `删除到空列表时补一行`() {
        var s = CalcState()
        repeat(5) { s = CalcActions.deleteCourse(s, 0) }
        assertEquals(1, s.courses.size)
    }

    @Test
    fun `五个字段独立设置`() {
        val s = CalcState()
            .let { CalcActions.setField(it, "moral", "95") }
            .let { CalcActions.setField(it, "pu", "8.5") }
            .let { CalcActions.setField(it, "psych", "90") }
        assertEquals("95", s.moral)
        assertEquals("8.5", s.pu)
        assertEquals("90", s.psych)
        assertEquals("", s.labor)
        assertEquals("", s.sport)
    }

    @Test
    fun `清空回到初始态`() {
        var s = CalcState()
            .let { CalcActions.inputCourse(it, 4, CourseField.SCORE, "88") }
            .let { CalcActions.setField(it, "moral", "95") }
        s = CalcActions.reset()
        assertEquals(5, s.courses.size)
        assertEquals("100", s.psych)
        assertEquals("", s.moral)
    }
}
