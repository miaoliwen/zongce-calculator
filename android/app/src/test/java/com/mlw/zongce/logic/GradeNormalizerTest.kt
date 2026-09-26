package com.mlw.zongce.logic

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** 对齐 crawler.py GradeCrawler 纯逻辑的断言（fixture 数据与 tests/_selftest_http.py 一致） */
class GradeNormalizerTest {

    private val json = Json

    /** 原始接口键 → 规范键（等价于 _collect_rows 的映射步骤） */
    private fun raw(vararg pairs: Pair<String, String?>): Map<String, String?> =
        pairs.associate { (k, v) -> (GradeNormalizer.mapField(k) ?: k) to v }

    @Test
    fun `合并课程号课程名并归一化`() {
        val row = GradeNormalizer.normalizeRow(
            raw(
                "xnxq" to "2025-2026-2",
                "kcmc" to "[CS1001]程序设计基础",
                "zhcj" to "82",
                "hdxf" to "4",
                "xf" to "",
                "fxcj" to "",
            )
        )
        assertEquals("2025-2026", row.year)
        assertEquals("2", row.term)
        assertEquals("CS1001", row.code)
        assertEquals("程序设计基础", row.name)
        assertEquals(82.0, row.score!!, 1e-9)
        assertEquals("82", row.scoreRaw)
        assertEquals(4.0, row.credit!!, 1e-9)
    }

    @Test
    fun `collectRows 从嵌套 jqGrid JSON 提取原始行`() {
        val payload = json.parseToJsonElement(
            """{"page":1,"total":1,"rows":[
                {"xnxq":"2025-2026-2","kcmc":"[CS1001]程序设计基础","zhcj":"82","hdxf":"4","xf":"","fxcj":""},
                {"other":{"kcmc":"[CS1002]数据库原理","zcj":"76","xf":"4"}}
            ]}"""
        )
        val out = mutableListOf<Map<String, String?>>()
        GradeNormalizer.collectRows(payload, out)
        assertEquals(2, out.size)
        assertEquals("xhr", out[0]["_src"])
        assertEquals("[CS1002]数据库原理", out[1]["name"])
        assertEquals("76", out[1]["score"])
    }

    @Test
    fun `merge 按 课程号课程名学年学期 去重互补`() {
        val a = raw(
            "kch" to "CS1001", "kcmc" to "程序设计基础",
            "xn" to "2025-2026", "xq" to "2", "cj" to "82",
        )
        val b = raw(
            "kch" to "CS1001", "kcmc" to "程序设计基础",
            "xn" to "2025-2026", "xq" to "2", "xf" to "4",
        )
        val merged = GradeNormalizer.merge(listOf(a, b))
        assertEquals(1, merged.size)
        assertEquals(82.0, merged[0].score!!, 1e-9)
        assertEquals(4.0, merged[0].credit!!, 1e-9)
    }

    @Test
    fun `学期硬过滤保留区间内的行`() {
        val rows = listOf(
            GradeRow("C1", "课程一", 1.0, "80", 80.0, null, null, null, "2025-2026", "1", null, listOf("xhr")),
            GradeRow("C2", "课程二", 2.0, "90", 90.0, null, null, null, "2025-2026", "2", null, listOf("xhr")),
            GradeRow("C3", "课程三", 3.0, "70", 70.0, null, null, null, "2024-2025", "2", null, listOf("xhr")),
            GradeRow("C4", "无学期", 1.0, "60", 60.0, null, null, null, null, null, null, listOf("xhr")),
        )
        val (kept, dropped) = GradeNormalizer.filterSemesters(rows, "2025-2026-1", "2025-2026-2")
        assertEquals(listOf("C1", "C2"), kept.map { it.code })
        assertEquals(2, dropped.size)
    }

    @Test
    fun `全部学期不过滤`() {
        val rows = listOf(
            GradeRow("C1", "课程一", 1.0, "80", 80.0, null, null, null, "2024-2025", "1", null, listOf("xhr")),
        )
        val (kept, dropped) = GradeNormalizer.filterSemesters(rows, "001", "001")
        assertEquals(1, kept.size)
        assertTrue(dropped.isEmpty())
    }

    @Test
    fun `体育课程剔除`() {
        val rows = listOf(
            GradeRow("PE1001", "体育", 2.0, "85.5", 85.5, null, null, null, "2025-2026", "2", null, listOf("xhr")),
            GradeRow("CS1001", "程序设计基础", 4.0, "82", 82.0, null, null, null, "2025-2026", "2", null, listOf("xhr")),
        )
        val (kept, excluded) = GradeNormalizer.excludeCourses(rows)
        assertEquals(listOf("CS1001"), kept.map { it.code })
        assertEquals(listOf("PE1001"), excluded.map { it.code })
    }

    @Test
    fun `起止区间取值逻辑`() {
        val item = SelectParser.SelectItem(
            "s", "s", "起始学年学期",
            listOf(
                SelectParser.SelectOption("001", "入学以来"),
                SelectParser.SelectOption("2025-2026-1", "2025-2026-1"),
                SelectParser.SelectOption("2025-2026-2", "2025-2026-2"),
            ),
        )
        // 两端都选具体学期 → 取小者为起始
        assertEquals(
            "2025-2026-1" to "2025-2026-2",
            GradeNormalizer.semesterRange(item, item, "2025-2026-2", "2025-2026-1"),
        )
        // 只选一个 → 起止相同
        assertEquals(
            "2025-2026-2" to "2025-2026-2",
            GradeNormalizer.semesterRange(item, item, "2025-2026-2", "__ALL__"),
        )
        // 都选“全部” → 页面自带 001
        assertEquals(
            "001" to "001",
            GradeNormalizer.semesterRange(item, item, "__ALL__", "__ALL__"),
        )
        // 无任何选项可用 → __ALL__
        assertEquals(
            "__ALL__" to "__ALL__",
            GradeNormalizer.semesterRange(null, null, "__ALL__", "__ALL__"),
        )
    }

    @Test
    fun `等级成绩无数值`() {
        val row = GradeNormalizer.normalizeRow(
            raw("kcmc" to "[GE9001]军事训练", "cjms" to "优秀", "hdxf" to "2")
        )
        assertNull(row.score)
        assertEquals("优秀", row.scoreRaw)
        assertEquals(2.0, row.credit!!, 1e-9)
    }
}
