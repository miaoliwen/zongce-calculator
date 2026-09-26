package com.mlw.zongce.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** 对齐 http_crawler.py 的页面解析断言 */
class SelectParserTest {

    private val contentPage = """<!DOCTYPE html><html><body>
<tr><td>起始学年学期
<select id="startXnxq" name="xnxq">
  <option value="001">入学以来</option>
  <option value="2025-2026-1">2025-2026-1</option>
  <option value="2025-2026-2">2025-2026-2</option>
</select></td></tr>
<tr><td>终止学年学期
<select id="endXnxq" name="xnxq2">
  <option value="001">入学以来</option>
  <option value="2025-2026-1">2025-2026-1</option>
  <option value="2025-2026-2">2025-2026-2</option>
</select></td></tr>
<script>
$("#xsdcjcxGridIdGrid").jqGrid({ url: '/admin/xsd/xsdcjcx/xsdQueryXscjList?gridtype=1', mtype: 'POST'});
</script>
</body></html>"""

    private val scanPage = """<!DOCTYPE html><html><body>
<input type="hidden" value="UUID123456" id="uuid"/>
<input type="hidden" value="ENC654321" id="enc"/>
<input type="hidden" value="https://mock/admin/scanLogin?" id="pcrefer"/>
<img src="/createqr?uuid=UUID123456" id="ewm"/>
</body></html>"""

    @Test
    fun `hidden input 解析兼容属性顺序`() {
        assertEquals("UUID123456", SelectParser.inputValue(scanPage, "uuid"))
        assertEquals("ENC654321", SelectParser.inputValue(scanPage, "enc"))
        assertEquals("", SelectParser.inputValue(scanPage, "missing"))
    }

    @Test
    fun `select 解析与筛选识别`() {
        val selects = SelectParser.parseSelects(contentPage)
        assertEquals(2, selects.size)
        val filters = SelectParser.buildFilters(selects) {}
        assertTrue(filters.semesterPair)
        assertNotNull(filters.year)
        assertNotNull(filters.term)
        assertTrue(filters.year!!.options.any { it.value == "2025-2026-2" })
        // 页面自带 001，不应注入合成“全部”
        assertTrue(filters.year!!.options.none { it.value == SelectParser.ALL_VALUE })
        assertTrue("起始" in filters.year!!.label)
        assertTrue("终止" in filters.term!!.label)
    }

    @Test
    fun `jqGrid 地址解析`() {
        assertEquals(
            "/admin/xsd/xsdcjcx/xsdQueryXscjList?gridtype=1",
            SelectParser.findGridUrl(contentPage),
        )
        assertNull(SelectParser.findGridUrl("<html>no grid</html>"))
    }

    @Test
    fun `缺全部选项时自动注入 __ALL__`() {
        val html = """<select id="y"><option value="2024-2025-1">2024-2025-1</option></select>"""
        val filters = SelectParser.buildFilters(SelectParser.parseSelects(html)) {}
        assertEquals(SelectParser.ALL_VALUE, filters.year!!.options.first().value)
    }

    @Test
    fun `学期文本识别`() {
        assertTrue(SelectParser.isSemesterText("2025-2026-2"))
        assertTrue(SelectParser.isSemesterText("2025/2026/2"))
        assertFalse(SelectParser.isSemesterText("入学以来"))
        assertTrue(SelectParser.isTerm(SelectParser.SelectItem("", "", "学期", listOf(
            SelectParser.SelectOption("1", "1"),
            SelectParser.SelectOption("2", "2"),
        ))))
    }

    @Test
    fun `grid 地址解析对齐 _resolve_grid_url`() {
        val base = "https://ntsf.jw.chaoxing.com"
        val content = "$base/admin//xsd/xsdcjcx/qbcjcx"
        // "/" 开头：按教务 BASE 拼，绝不能拼到 passport 域（真实案例：拼错域名收到 403 Forbidden）
        assertEquals(
            "$base/admin/xsd/xsdcjcx/xsdQueryXscjList",
            SelectParser.resolveGridUrl(content, "/admin/xsd/xsdcjcx/xsdQueryXscjList", base),
        )
        // 相对文件名：以内容页目录为基准（保留 // 双斜杠）
        assertEquals(
            "$base/admin//xsd/xsdcjcx/xsdQueryXscjList",
            SelectParser.resolveGridUrl(content, "xsdQueryXscjList", base),
        )
        // 完整 URL 原样返回
        assertEquals(
            "https://other.example.com/api/x",
            SelectParser.resolveGridUrl(content, "https://other.example.com/api/x", base),
        )
    }
}
