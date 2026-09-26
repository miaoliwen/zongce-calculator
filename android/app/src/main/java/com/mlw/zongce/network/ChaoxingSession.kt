package com.mlw.zongce.network

import com.mlw.zongce.logic.CrawlOutcome
import com.mlw.zongce.logic.GradeNormalizer
import com.mlw.zongce.logic.GradeRow
import com.mlw.zongce.logic.SelectParser
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlin.random.Random

/** 扫码登录信息：uuid/enc 可直接构造确认页 deep link（M0 验证结论），二维码仅跨设备兜底 */
data class ScanInfo(
    val uuid: String,
    val enc: String,
    val pcrefer: String,
    val deepLink: String,
    val qrUrl: String?,
    val qrBytes: ByteArray?,
)

sealed class ScanPoll {
    data object Idle : ScanPoll()
    data class Waiting(val reset: Boolean) : ScanPoll()
    data class Scanned(val nick: String) : ScanPoll()
    data object Confirmed : ScanPoll()
    data object Expired : ScanPoll()
    data class Error(val message: String) : ScanPoll()
}

/** 抓取进度回调：(阶段, 百分比, 明细) */
typealias ProgressFn = (stage: String, pct: Double?, detail: String) -> Unit

/**
 * 超星扫码登录 + 成绩抓取会话（单用户版，移植自 http_crawler.py 的 HttpGradeCrawler）。
 * 多用户管理/令牌鉴权/诊断落盘均为服务端概念，App 内不需要。
 */
class ChaoxingSession(
    private val log: (String) -> Unit = {},
    private val progress: ProgressFn = { _, _, _ -> },
) {

    val http = ChaoxingHttp(cookieJar = SessionCookieJar(), log = log)

    // 扫码状态
    var currentScan: ScanInfo? = null
        private set
    private var startedAt = 0.0
    private var pollErrors = 0

    // 成绩页状态
    private var contentUrl: String? = null
    private var gridUrl: String? = null
    private var lastCrawlAt = 0L

    val isLoggedIn: Boolean
        get() = contentUrl != null

    /** 当前二维码剩余有效秒数（官方 TTL 150s） */
    fun ttlLeftSeconds(): Int =
        (Cx.QR_TTL_SECONDS - (System.currentTimeMillis() / 1000.0 - startedAt))
            .toInt().coerceAtLeast(0)

    // ---------------- 扫码登录 ---------------- //

    /** 打开扫码页：解析 uuid/enc/pcrefer，构造确认页 deep link，下载二维码（兜底用） */
    suspend fun openScan(): ScanInfo {
        val html = http.text("GET", Cx.SCAN_URL, timeoutSeconds = 20)
        val uuid = SelectParser.inputValue(html, "uuid")
        val enc = SelectParser.inputValue(html, "enc")
        var pcrefer = SelectParser.inputValue(html, "pcrefer").ifEmpty { Cx.BASE + "/admin/scanLogin?" }
        val imgRe = Regex("<img\\b[^>]*\\bid=[\"']ewm[\"'][^>]*>",
            setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
        val qrSrc = imgRe.find(html)?.let { SelectParser.attr(it.value, "src") } ?: ""
        if (uuid.isEmpty() || qrSrc.isEmpty()) {
            throw CrawlException("二维码页面未返回 uuid/二维码地址")
        }
        pcrefer = pcrefer.ifEmpty { Cx.BASE + "/admin/scanLogin?" }

        // 二维码图片：跨设备扫码兜底。下载失败不阻塞流程（deep link 是主路径）
        var qrBytes: ByteArray? = null
        var qrUrl: String? = null
        runCatching {
            val url = resolveUrl(Cx.SCAN_URL, qrSrc)
            qrUrl = url
            val img = http.getBytes(
                url,
                headers = mapOf(
                    "Referer" to Cx.SCAN_URL,
                    "Accept" to "image/avif,image/webp,image/png,*/*",
                ),
                timeoutSeconds = 20,
            )
            if (img.code == 200 && img.contentType.contains("image")) {
                qrBytes = img.bytes
            }
            if (qrBytes == null || qrBytes!!.size < 50) {
                qrBytes = null
                log("二维码图片拉取异常，改用「打开确认页」方式登录")
            }
        }.onFailure { log("二维码图片拉取失败，改用「打开确认页」方式登录") }

        startedAt = System.currentTimeMillis() / 1000.0
        pollErrors = 0
        val info = ScanInfo(
            uuid = uuid,
            enc = enc,
            pcrefer = pcrefer,
            deepLink = Cx.toAuthLoginUrl(uuid, enc),
            qrUrl = qrUrl,
            qrBytes = qrBytes,
        )
        currentScan = info
        log("登录信息已生成，等待手机确认…")
        return info
    }

    /** 二维码失效后重新加载 */
    suspend fun refreshScan(): ScanInfo = openScan()

    /** 清空会话 cookie 与登录态（引擎对象保留，下次扫码直接复用） */
    suspend fun logout() {
        http.cookieJar.clear()
        contentUrl = null
        gridUrl = null
        currentScan = null
        startedAt = 0.0
        pollErrors = 0
        log("已清除登录状态，可重新扫码登录")
    }

    /**
     * 轮询扫码状态（与官方页面同参数：POST getauthstatus enc+uuid，3s/次由调用方控制）。
     * 单次轮询失败静默容忍，连续 5 次才报错（MAX_POLL_ERRORS）。
     */
    suspend fun pollState(): ScanPoll {
        val scan = currentScan ?: return ScanPoll.Idle
        val now = System.currentTimeMillis() / 1000.0
        if (now - startedAt > Cx.QR_TTL_SECONDS) return ScanPoll.Expired

        val data: kotlinx.serialization.json.JsonElement = try {
            http.json(
                "POST", Cx.AUTHSTATUS_URL,
                form = mapOf("enc" to scan.enc, "uuid" to scan.uuid),
                retries = 1, timeoutSeconds = 20,
            )
        } catch (e: CrawlException) {
            pollErrors += 1
            return if (pollErrors >= 5) ScanPoll.Error(e.message ?: "轮询失败") else ScanPoll.Waiting(false)
        }
        pollErrors = 0

        if (jsonTruthy((data as? JsonObject)?.get("status"))) {
            return if (establishSession(scan)) {
                log("扫码登录成功")
                ScanPoll.Confirmed
            } else {
                ScanPoll.Waiting(false)
            }
        }
        // type 实测可能是字符串（"4"），用 doubleOrNull 统一兼容数字与字符串
        val t = ((data as? JsonObject)?.get("type") as? JsonPrimitive)?.doubleOrNull
        return when (t) {
            4.0 -> ScanPoll.Scanned(
                ((data as? JsonObject)?.get("nickname") as? JsonPrimitive)?.content ?: ""
            )
            6.0 -> {
                // 用户在手机上取消：官方页面直接 reload，这里重发登录信息并继续等待
                log("用户取消了扫码，已重置")
                openScan()
                ScanPoll.Waiting(true)
            }
            2.0 -> ScanPoll.Expired  // 服务端 mes="二维码已失效"
            else -> ScanPoll.Waiting(false)
        }
    }

    /** 手机确认后访问 pcrefer 建立教务会话；教务侧可能有短暂延迟，做有限重试 */
    private suspend fun establishSession(scan: ScanInfo): Boolean {
        repeat(3) { attempt ->
            try {
                http.getDiscard(scan.pcrefer, retries = 1, timeoutSeconds = 30)
                val (finalUrl, code) = http.getDiscard(Cx.GRADES_URL, retries = 1, timeoutSeconds = 30)
                if (!finalUrl.contains("/admin/login") && code == 200) {
                    return true
                }
            } catch (_: CrawlException) {
                // 教务侧延迟，稍后重试
            }
            if (attempt < 2) delay(1000)
        }
        return false
    }

    // ---------------- 学年学期选项 ---------------- //

    /** 打开「全部成绩查询」内容页（首次成功路径记入 contentUrl） */
    private suspend fun openContentPage(): String {
        val notes = mutableListOf<String>()
        for (path in Cx.GRADES_CONTENT_PATHS) {
            try {
                val page = http.getText(Cx.BASE + path, timeoutSeconds = 30)
                if (page.finalUrl.contains("/admin/login")) {
                    throw CrawlException("会话已失效，请重新扫码登录")
                }
                if (page.code == 200) {
                    contentUrl = page.finalUrl
                    return page.body
                }
                notes.add("$path: HTTP ${page.code}（最终 ${page.finalUrl}）")
            } catch (e: CrawlException) {
                notes.add("$path: ${e.message}")
            }
        }
        throw CrawlException("无法打开成绩查询页；" + notes.joinToString("；"))
    }

    /** 读取教务系统的学年/学期真实选项（对应 /api/options） */
    suspend fun discoverFilters(): SelectParser.FilterInfo {
        val html = openContentPage()
        val grid = SelectParser.findGridUrl(html)
        gridUrl = grid
        val selects = SelectParser.parseSelects(html)
        return SelectParser.buildFilters(selects, log)
    }

    // ---------------- 抓取 ---------------- //

    private suspend fun delay(ms: Long) = kotlinx.coroutines.delay(ms)

    /** passport2 域内相对地址解析（二维码图片用；/ 开头按 passport 根拼接） */
    private fun resolveUrl(baseUrl: String, path: String): String = when {
        path.startsWith("http") -> path
        path.startsWith("/") -> Cx.PASSPORT + path
        else -> {
            // 相对路径：以内容页所在目录为基准
            val idx = baseUrl.lastIndexOf('/')
            baseUrl.substring(0, idx + 1) + path
        }
    }

    /** jqGrid 取数（逐语义移植 _query_grades：分页、页间随机停 0.5~1s、进度 8%→88%） */
    private suspend fun queryGrades(startV: String, endV: String): List<Map<String, String?>> {
        val grid = gridUrl ?: throw CrawlException(
            "未能从成绩页解析出 jqGrid 接口地址（页面可能改版），请稍后重试。"
        )
        val url = SelectParser.resolveGridUrl(contentUrl ?: Cx.BASE, grid, Cx.BASE)
        val rawRows = mutableListOf<Map<String, String?>>()
        var page = 1
        var totalPages = 1
        while (page <= totalPages) {
            if (page > 1) {
                // 多页抓取放慢节奏：页间随机停 0.5~1s，不对教务系统连续快查
                delay(Random.nextLong(500, 1000))
            }
            val payload = http.json(
                "POST", url,
                form = mapOf(
                    "xnxq" to startV,
                    "xnxq2" to endV,
                    "_search" to "false",
                    "nd" to System.currentTimeMillis().toString(),
                    "rows" to Cx.PAGE_ROWS.toString(),
                    "page" to page.toString(),
                    "sidx" to "",
                    "sord" to "asc",
                ),
                headers = mapOf(
                    "X-Requested-With" to "XMLHttpRequest",
                    "Referer" to (contentUrl ?: Cx.BASE),
                    "Accept" to "application/json, text/javascript, */*; q=0.01",
                ),
                timeoutSeconds = 120,
            )
            if (page == 1) {
                val total = ((payload as? JsonObject)?.get("total") as? JsonPrimitive)?.doubleOrNull
                totalPages = maxOf(1, total?.toInt() ?: 1)
            }
            val pageRows = mutableListOf<Map<String, String?>>()
            GradeNormalizer.collectRows(payload, pageRows)
            rawRows.addAll(pageRows)
            progress(
                "抓取成绩",
                minOf(88.0, 8.0 + 80.0 * page / totalPages),
                "第 $page/$totalPages 页 · 累计 ${rawRows.size} 条",
            )
            if (page >= totalPages || pageRows.isEmpty()) break
            page += 1
        }
        return rawRows
    }

    /** 抓取主流程（对应 _crawl）：查询 → 归一化 → 学期硬过滤 → 课程剔除 */
    suspend fun crawl(
        yearValue: String,
        termValue: String,
        filters: SelectParser.FilterInfo?,
    ): CrawlOutcome {
        val left = Cx.CRAWL_MIN_INTERVAL_MS - (System.currentTimeMillis() - lastCrawlAt)
        if (lastCrawlAt > 0 && left > 0) {
            throw CrawlException("刚刚抓取过，请约 ${left / 1000 + 1} 秒后再试（降低对教务系统的压力）")
        }

        progress("准备查询", 2.0, "")
        val effective = filters ?: discoverFilters()
        if (gridUrl == null) {
            // 传入 filters 但内容页尚未打开过：补开内容页拿 jqGrid 地址
            val html = openContentPage()
            gridUrl = SelectParser.findGridUrl(html)
        }

        val yearItem = effective.year
        val termItem = effective.term
        val startV: String
        val endV: String
        if (effective.semesterPair) {
            val (s, e) = GradeNormalizer.semesterRange(yearItem, termItem, yearValue, termValue)
            startV = s; endV = e
            log("学年学期区间：起始 $startV → 终止 $endV")
        } else {
            var s = if (yearValue != GradeNormalizer.ALL_VALUE) yearValue else termValue
            var e = if (termValue != GradeNormalizer.ALL_VALUE) termValue else s
            if (s == GradeNormalizer.ALL_VALUE) {
                s = GradeNormalizer.SEMESTER_ALL_VALUE
                e = GradeNormalizer.SEMESTER_ALL_VALUE
            }
            startV = s; endV = e
        }

        progress("提交查询", 6.0, "$startV~$endV")
        val raw = queryGrades(startV, endV)
        log("接口返回原始记录 ${raw.size} 条")

        val merged = GradeNormalizer.merge(raw)
        log("合并去重后共 ${merged.size} 门课程")
        progress("解析合并", 90.0, "共 ${merged.size} 门课程")
        if (merged.isEmpty()) {
            throw CrawlException("未解析到任何成绩。请确认所选学年学期已有成绩，稍后重试。")
        }

        val (kept, droppedSem) = GradeNormalizer.filterSemesters(merged, startV, endV)
        if (kept.isEmpty()) {
            throw CrawlException("所选学年学期（$startV~$endV）下没有查到成绩。")
        }
        if (droppedSem.isNotEmpty()) {
            log(
                "按学年学期 $startV~$endV 过滤：保留 ${kept.size} 门，剔除 ${droppedSem.size} 门其它学期"
            )
        }

        val (finalRows, excluded) = GradeNormalizer.excludeCourses(kept)
        log(
            "最终 ${finalRows.size} 门纳入专业成绩" +
                (if (excluded.isNotEmpty()) "，另剔除 ${excluded.size} 门（不纳入专业成绩）" else "")
        )
        progress("完成", 100.0, "${finalRows.size} 门课程")
        lastCrawlAt = System.currentTimeMillis()

        val sorted = finalRows.sortedWith(
            compareBy({ it.year ?: "" }, { it.term ?: "" }, { it.code ?: "" })
        )
        return CrawlOutcome(rows = sorted, excluded = excluded, droppedBySemester = droppedSem)
    }

    // ---------------- JSON 工具 ---------------- //

    /** Python truthiness：None/False/""/0 为假，其余为真；
     *  额外把字符串 "true"/"false"/"0"/"1" 按布尔处理（服务端实测会把布尔/数字序列化成字符串）。 */
    private fun jsonTruthy(e: kotlinx.serialization.json.JsonElement?): Boolean = when (e) {
        null, is JsonNull -> false
        is JsonPrimitive ->
            if (e.isString) when (e.content.lowercase()) {
                "true", "1" -> true
                "false", "0", "" -> false
                else -> true
            } else e.booleanOrNull ?: ((e.doubleOrNull ?: 0.0) != 0.0)
        is JsonArray -> e.isNotEmpty()
        is JsonObject -> true
    }
}
