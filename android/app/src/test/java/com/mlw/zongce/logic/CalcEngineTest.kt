package com.mlw.zongce.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 与网页版 compute() 对拍的公式测试。
 * 满分场景：0.6×100 + 0.2×80 + 0.1×80 + 0.05×100 + 0.05×80 = 93.00
 */
class CalcEngineTest {

    private fun state(
        courses: List<Pair<String, String>> = emptyList(),
        moral: String = "",
        labor: String = "",
        pu: String = "",
        sport: String = "",
        psych: String = "100",
    ) = CalcState(
        courses = courses.map { CourseEntry(it.first, it.second) },
        moral = moral, labor = labor, pu = pu, sport = sport, psych = psych,
    )

    @Test
    fun `满分场景等于93`() {
        val r = CalcEngine.compute(
            state(
                courses = listOf("100" to "1", "100" to "2"),
                moral = "100", labor = "100", pu = "10", sport = "100", psych = "100",
            )
        )
        assertEquals(100.0, r.academic, 1e-9)
        assertEquals(80.0, r.moral, 1e-9)
        assertEquals(80.0, r.labor, 1e-9)
        assertEquals(100.0, r.ability, 1e-9)
        assertEquals(80.0, r.body, 1e-9)
        assertEquals(93.0, r.total, 1e-9)
        assertEquals("93.00", CalcEngine.format(r.total))
    }

    @Test
    fun `空状态心理学默认100使身心素质为40`() {
        val r = CalcEngine.compute(state())
        assertEquals(0.0, r.academic, 1e-9)
        assertEquals(40.0, r.body, 1e-9)
        assertEquals(2.0, r.total, 1e-9) // 0.05 × 40
    }

    @Test
    fun `加权平均按学分`() {
        // (80×3 + 90×2) ÷ 5 = 84
        val r = CalcEngine.compute(state(courses = listOf("80" to "3", "90" to "2")))
        assertEquals(84.0, r.academic, 1e-9)
        assertEquals(420.0, r.sumWC, 1e-9)
        assertEquals(5.0, r.sumC, 1e-9)
    }

    @Test
    fun `无效行不参与加权`() {
        // 学分为0 / 成绩为空 / 学分为空的行都跳过
        val r = CalcEngine.compute(
            state(
                courses = listOf(
                    "80" to "3",  // 有效
                    "" to "3",    // 成绩为空 → 跳过
                    "70" to "0",  // 学分为 0 → 跳过
                    "90" to "",   // 学分为空 → num("")=0 → 跳过
                )
            )
        )
        assertEquals(80.0, r.academic, 1e-9)
        assertEquals(3.0, r.sumC, 1e-9)
    }

    @Test
    fun `PU分超过10按100封顶`() {
        assertEquals(100.0, CalcEngine.compute(state(pu = "12")).ability, 1e-9)
        assertEquals(85.0, CalcEngine.compute(state(pu = "8.5")).ability, 1e-9)
    }

    @Test
    fun `parseFloat对齐JS前缀解析`() {
        assertEquals(88.0, CalcEngine.num("88abc"), 1e-9)
        assertEquals(0.5, CalcEngine.num(".5"), 1e-9)
        assertEquals(-2.0, CalcEngine.num("-2.0x"), 1e-9)
        assertEquals(0.0, CalcEngine.num(""), 1e-9)
        assertEquals(0.0, CalcEngine.num("abc"), 1e-9)
        assertTrue(CalcEngine.has("88abc"))
        assertFalse(CalcEngine.has(""))
        assertFalse(CalcEngine.has("abc"))
    }

    @Test
    fun `format两位小数`() {
        assertEquals("93.00", CalcEngine.format(93.0))
        assertEquals("84.33", CalcEngine.format(84.333333))
        assertEquals("0.00", CalcEngine.format(0.0))
    }
}
