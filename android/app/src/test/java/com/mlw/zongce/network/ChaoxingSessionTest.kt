package com.mlw.zongce.network

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

/**
 * 纯 HTTP 引擎离线自测（移植 tests/_selftest_http.py，MockWebServer 模拟超星）。
 * 虚构数据与原 fixture 一致；不需要真实教务系统。
 */
class ChaoxingSessionTest {

    // 虚构成绩数据（与 _selftest_http.py ROWS 一致；非真实成绩单）
    private val rows: List<List<String>> = listOf(
        listOf("2025-2026-2", "CS1001", "程序设计基础", "82", "4"),
        listOf("2025-2026-2", "CS1002", "数据库原理", "76", "4"),
        listOf("2025-2026-2", "CS1003", "计算机网络", "88", "4"),
        listOf("2025-2026-2", "GE2001", "思想道德与法治", "81", "3"),
        listOf("2025-2026-2", "GE2002", "军事理论", "90", "1"),
        listOf("2025-2026-2", "PE1001", "体育", "85.5", "2"),
        listOf("2025-2026-2", "CS1004", "数据结构与算法", "78", "4"),
        listOf("2025-2026-2", "GE2003", "大学英语", "83.5", "4"),
        listOf("2025-2026-1", "MA1001", "高等数学", "74", "4"),
        listOf("2025-2026-1", "CS1005", "操作系统", "69", "3"),
        listOf("2025-2026-1", "CS1006", "Python程序设计", "91", "4"),
        listOf("2025-2026-1", "GE2004", "艺术鉴赏", "95.5", "1"),
    )

    private val authCalls = AtomicInteger(0)
    private val server = MockWebServer()
    private lateinit var base: String

    private fun scanPage() = """<!DOCTYPE html><html><body>
<input type="hidden" value="UUID123456" id="uuid"/>
<input type="hidden" value="ENC654321" id="enc"/>
<input type="hidden" value="$base/admin/scanLogin?" id="pcrefer"/>
<img src="/createqr?uuid=UUID123456" id="ewm"/>
</body></html>"""

    private fun contentPage() = """<!DOCTYPE html><html><body>
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

    private fun gridPayload(form: Map<String, String>): String {
        var lo = form["xnxq"] ?: ""
        var hi = form["xnxq2"] ?: ""
        if (lo == "001") lo = ""
        if (hi == "001") hi = ""
        val filtered = rows.filter { r ->
            (lo.isEmpty() || r[0] >= lo) && (hi.isEmpty() || r[0] <= hi)
        }
        val rowsJson = filtered.joinToString(",") { r ->
            buildJsonObject {
                put("xnxq", r[0])
                put("kcmc", "[" + r[1] + "]" + r[2])
                put("zhcj", r[3])
                put("hdxf", r[4].toString())
                put("xf", "")
                put("fxcj", "")
            }.toString()
        }
        return """{"page":1,"total":1,"records":${filtered.size},"rows":[$rowsJson]}"""
    }

    @Before
    fun setUp() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.requestUrl?.encodedPath ?: ""
                return when {
                    path.contains("cloudscanlogin") -> MockResponse().setBody(scanPage())
                    path == "/createqr" -> MockResponse()
                        .addHeader("Content-Type", "image/png")
                        .setBody(okio.Buffer().write(ByteArray(260) { 0x7F }))
                    path == "/admin/scanLogin" -> MockResponse()
                        .setResponseCode(303)
                        .addHeader("Location", "/admin/indexMain/M1402")
                        .addHeader("Set-Cookie", "JWSESSIONID=abc; Path=/")
                        .setBody("")
                    path == "/admin/indexMain/M1402" -> MockResponse().setBody("<html>portal</html>")
                    path.contains("qbcjcx") -> MockResponse().setBody(contentPage())
                    path == "/getauthstatus" -> {
                        val n = authCalls.incrementAndGet()
                        when {
                            n <= 2 -> MockResponse()
                                .addHeader("Content-Type", "application/json")
                                .setBody("{}")
                            n == 3 -> MockResponse()
                                .addHeader("Content-Type", "application/json")
                                .setBody("""{"type":4,"nickname":"小明","uid":"123"}""")
                            else -> MockResponse()
                                .addHeader("Content-Type", "application/json")
                                .addHeader("Set-Cookie", "UID=1; Path=/")
                                .setBody("""{"status":1}""")
                        }
                    }
                    path.contains("xsdQueryXscjList") -> {
                        val form = parseForm(request.body.readUtf8())
                        MockResponse()
                            .addHeader("Content-Type", "application/json")
                            .setBody(gridPayload(form))
                    }
                    else -> MockResponse().setResponseCode(404).setBody("not found: $path")
                }
            }
        }
        server.start()
        base = server.url("/").toString().trimEnd('/')
        Cx.BASE = base
        Cx.PASSPORT = base
    }

    @After
    fun tearDown() {
        server.shutdown()
        Cx.BASE = "https://ntsf.jw.chaoxing.com"
        Cx.PASSPORT = "https://passport2.chaoxing.com"
    }

    private fun parseForm(raw: String): Map<String, String> =
        raw.split("&").filter { "=" in it }.associate {
            val (k, v) = it.split("=", limit = 2)
            java.net.URLDecoder.decode(k, "UTF-8") to java.net.URLDecoder.decode(v, "UTF-8")
        }

    private fun newSession() = ChaoxingSession()

    // ---------------- 断言（对齐 _selftest_http.py） ---------------- //

    @Test
    fun `扫码登录与单学期抓取全流程`() = runTest {
        val session = newSession()

        // open_scan_page
        val info = session.openScan()
        assertTrue("二维码字节已获取", info.qrBytes != null && info.qrBytes!!.size >= 50)
        assertEquals("uuid 解析正确", "UUID123456", info.uuid)
        assertTrue("deep link 含 uuid", info.deepLink.contains("UUID123456"))
        assertTrue("deep link 含 enc", info.deepLink.contains("ENC654321"))

        // 轮询：waiting → waiting → scanned → confirmed
        val states = mutableListOf<ScanPoll>()
        repeat(6) {
            val st = session.pollState()
            states.add(st)
            if (st is ScanPoll.Confirmed) return@repeat
        }
        assertTrue("状态序列含 waiting", states.any { it is ScanPoll.Waiting })
        assertTrue("状态序列含 scanned", states.any { it is ScanPoll.Scanned })
        assertEquals("最终 confirmed", ScanPoll.Confirmed, states.last())
        val scanned = states.filterIsInstance<ScanPoll.Scanned>().first()
        assertEquals("昵称解析", "小明", scanned.nick)

        // discover_filters
        val filters = session.discoverFilters()
        assertTrue("识别为起止区间模式", filters.semesterPair)
        assertNotNull(filters.year)
        assertNotNull(filters.term)
        assertTrue("选项含 2025-2026-2", filters.year!!.options.any { it.value == "2025-2026-2" })

        // 单学期抓取
        authCalls.set(99) // 避免后续误触扫码状态机
        val outcome = session.crawl("2025-2026-2", "2025-2026-2", filters)
        assertEquals("2025-2026-2 返回 7 门（8 门剔除体育）", 7, outcome.rows.size)
        assertTrue(
            "全部属于 2025-2026 学年第 2 学期",
            outcome.rows.all { it.year == "2025-2026" && it.term == "2" },
        )
        assertTrue("体育已被剔除", outcome.rows.all { "体育" !in (it.name ?: "") })
        assertTrue("剔除项含体育", outcome.excluded.any { "体育" in (it.name ?: "") })
        val sample = outcome.rows.first { it.code == "CS1001" }
        assertEquals("成绩归一化正确", 82.0, sample.score!!, 1e-9)
        assertEquals("学分归一化正确", 4.0, sample.credit!!, 1e-9)
        assertEquals("等级成绩单列（军事理论为数字，此处应为 0）", 0, com.mlw.zongce.logic.GradeImport.gradedCount(outcome))
    }

    @Test
    fun `全部学期抓取与登录态判断`() = runTest {
        val session = newSession()
        assertNull("未登录时 isLoggedIn 为 false", if (session.isLoggedIn) Unit else null)

        // 直接注入登录信息（对齐 eng2 的做法），不重复扫码
        session.openScan()
        val outcome = session.crawl("__ALL__", "__ALL__", null)
        assertEquals("入学以来返回 11 门（12 门剔除体育）", 11, outcome.rows.size)
        assertEquals(
            "跨学期数据齐全",
            setOf("1", "2"),
            outcome.rows.mapNotNull { it.term }.toSet(),
        )
    }

    @Test
    fun `60秒最小抓取间隔生效`() = runTest {
        val session = newSession()
        session.openScan()
        session.crawl("__ALL__", "__ALL__", null)
        val e = runCatching { session.crawl("__ALL__", "__ALL__", null) }.exceptionOrNull()
        assertTrue("连续抓取应被限频拦截", e is CrawlException && e.message!!.contains("秒"))
    }
}
