package com.mlw.zongce.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.FormBody
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.Response
import java.util.concurrent.ConcurrentHashMap

/** 教务/超星端点与节奏常量（crawler.py / http_crawler.py 原值；var 供测试注入 mock 地址） */
object Cx {
    var BASE = "https://ntsf.jw.chaoxing.com"
    var PASSPORT = "https://passport2.chaoxing.com"
    val GRADES_URL: String get() = "$BASE/admin/indexMain/M1402"
    val GRADES_CONTENT_PATHS = listOf("/admin//xsd/xsdcjcx/qbcjcx", "/admin/xsd/xsdcjcx/qbcjcx")

    // pcrefer 必须与教务系统回调一致；编码方式与 crawler.py quote(safe="") 对齐
    private fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8")
    val SCAN_URL: String
        get() = "$PASSPORT/cloudscanlogin?pcrefer=" + enc("$BASE/admin/scanLogin?") +
            "&customurl=&mobiletip=" + enc("教务管理系统")
    val AUTHSTATUS_URL: String get() = "$PASSPORT/getauthstatus"

    /** 二维码解码出的确认页 URL 模板（M0 实测：uuid/enc 均在内容里，可直接构造） */
    fun toAuthLoginUrl(uuid: String, encVal: String): String =
        "$PASSPORT/toauthlogin?uuid=$uuid&enc=$encVal&xxtrefer=&clientid=&type=1&mobiletip=" +
            enc("教务管理系统")

    /** 桌面 Chrome UA：服务端按 UA 出页面，不能换成手机 UA */
    const val UA =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    const val QR_TTL_SECONDS = 150          // 官方 3s×50 次后判失效
    const val POLL_INTERVAL_MS = 3000L      // 与官方页面同节奏
    const val PAGE_ROWS = 500
    const val CRAWL_MIN_INTERVAL_MS = 60_000L  // 同一用户 60s 内不重复抓取
}

/** 业务错误：信息可读、不含敏感内容，直接展示给用户 */
class CrawlException(message: String) : Exception(message)

/** GET 文本结果：code=状态码，finalUrl=重定向后的最终 URL，body=UTF-8 文本 */
data class FinalText(val code: Int, val finalUrl: String, val body: String)

/** GET 二进制结果：code=状态码，contentType=响应 content-type，bytes=原始字节 */
data class FetchedBytes(val code: Int, val contentType: String, val bytes: ByteArray)

/** 会话 cookie（内存 + 可持久化快照） */
@Serializable
data class CookieSnapshot(
    val name: String, val value: String, val domain: String, val path: String,
    val expiresAt: Long, val secure: Boolean, val httpOnly: Boolean, val hostOnly: Boolean,
)

class SessionCookieJar : CookieJar {

    private val store = ConcurrentHashMap<String, Cookie>()

    override fun loadForRequest(url: HttpUrl): List<Cookie> {
        val now = System.currentTimeMillis()
        return store.values.filter { c ->
            c.expiresAt > now && c.matches(url)
        }
    }

    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        for (c in cookies) store[dedupeKey(c)] = c
    }

    /** OkHttp 未公开 Cookie.key，用 name+domain+path+secure 自拼去重键 */
    private fun dedupeKey(c: Cookie) = "${c.name}|${c.domain}|${c.path}|${c.secure}"

    fun clear() = store.clear()

    fun snapshot(): List<CookieSnapshot> = store.values.map {
        CookieSnapshot(it.name, it.value, it.domain, it.path,
            it.expiresAt, it.secure, it.httpOnly, it.hostOnly)
    }

    fun restore(snapshots: List<CookieSnapshot>) {
        for (s in snapshots) {
            runCatching {
                val builder = Cookie.Builder()
                    .name(s.name)
                    .value(s.value)
                    .domain(s.domain)
                    .path(s.path)
                    .expiresAt(s.expiresAt)
                if (s.secure) builder.secure()
                if (s.httpOnly) builder.httpOnly()
                if (s.hostOnly) builder.hostOnlyDomain(s.domain)
                val c = builder.build()
                store[dedupeKey(c)] = c
            }
        }
    }
}

/**
 * 带限频退避的 HTTP 引擎（逐语义移植 http_crawler.py 的 _request/_json）：
 *  - 网络异常 / 5xx：短退避 0.6s→1.2s，共 3 次；
 *  - 429：长退避 5s→10s（上限 30s）+ 跨请求冷却 30→60→120s 指数增长，任一成功复位，
 *    冷却期内每个请求最多先等 10s；
 *  - 响应一律按 UTF-8 解码（超星页面为 UTF-8）。
 */
class ChaoxingHttp(
    val cookieJar: SessionCookieJar = SessionCookieJar(),
    private val log: (String) -> Unit = {},
) {

    private val baseClient = OkHttpClient.Builder()
        .cookieJar(cookieJar)
        .connectTimeout(java.time.Duration.ofSeconds(20))
        .readTimeout(java.time.Duration.ofSeconds(120))
        .build()

    private val mutex = Mutex()
    private var rateHits = 0
    private var cooldownUntil = 0L

    companion object {
        private const val DEFAULT_RETRIES = 2
        private const val RETRY_BACKOFF_MS = 600L
        private val RETRY_STATUS = setOf(429, 500, 502, 503, 504)
        private const val RATE_LIMIT_BACKOFF_MS = 5000L
        private const val RATE_LIMIT_PACE_MAX_MS = 10_000L
        private const val RATE_LIMIT_COOLDOWN_MAX_MS = 120_000L
        private val json = Json { ignoreUnknownKeys = true }
    }

    var lastNonJsonHint: String? = null
        private set

    private suspend fun get(
        url: String,
        headers: Map<String, String> = emptyMap(),
        retries: Int = DEFAULT_RETRIES,
        timeoutSeconds: Long = 30,
    ): Response = request("GET", url, null, headers, retries, timeoutSeconds)

    /** GET 文本 + 最终 URL（整个"执行→读取→关闭"都在 IO 线程；内容页用） */
    suspend fun getText(
        url: String,
        headers: Map<String, String> = emptyMap(),
        retries: Int = DEFAULT_RETRIES,
        timeoutSeconds: Long = 30,
    ): FinalText = withContext(Dispatchers.IO) {
        request("GET", url, null, headers, retries, timeoutSeconds).use { resp ->
            val body = resp.body?.bytes()?.toString(Charsets.UTF_8) ?: ""
            FinalText(resp.code, resp.request.url.toString(), body)
        }
    }

    /** GET 二进制（二维码图片）；整个周期在 IO 线程 */
    suspend fun getBytes(
        url: String,
        headers: Map<String, String> = emptyMap(),
        retries: Int = DEFAULT_RETRIES,
        timeoutSeconds: Long = 30,
    ): FetchedBytes = withContext(Dispatchers.IO) {
        request("GET", url, null, headers, retries, timeoutSeconds).use { resp ->
            FetchedBytes(resp.code, resp.header("content-type") ?: "", resp.body?.bytes() ?: ByteArray(0))
        }
    }

    /** GET 并完整读取后丢弃响应体（建立会话/预热用）；返回 (最终 URL, 状态码)。
     *  必须读完整再关：直接 close 未读的响应会在调用线程触发 OkHttp 排空读取（主线程会崩）。 */
    suspend fun getDiscard(
        url: String,
        headers: Map<String, String> = emptyMap(),
        retries: Int = DEFAULT_RETRIES,
        timeoutSeconds: Long = 30,
    ): Pair<String, Int> = withContext(Dispatchers.IO) {
        request("GET", url, null, headers, retries, timeoutSeconds).use { resp ->
            runCatching { resp.body?.bytes() }
            resp.request.url.toString() to resp.code
        }
    }

    suspend fun postForm(
        url: String,
        form: Map<String, String>,
        headers: Map<String, String> = emptyMap(),
        retries: Int = DEFAULT_RETRIES,
        timeoutSeconds: Long = 30,
    ): Response {
        val body = FormBody.Builder().apply { form.forEach { (k, v) -> add(k, v) } }.build()
        return request("POST", url, body, headers, retries, timeoutSeconds)
    }

    suspend fun request(
        method: String,
        url: String,
        body: RequestBody?,
        headers: Map<String, String>,
        retries: Int,
        timeoutSeconds: Long,
    ): Response {
        // 此前被限流过：先小步冷却再发，避免顶着 429 连续请求
        mutex.withLock {
            val wait = cooldownUntil - System.currentTimeMillis()
            if (wait > 0) {
                val pause = minOf(wait, RATE_LIMIT_PACE_MAX_MS)
                log("限流冷却中，先等待 ${pause / 1000 + 1} 秒…")
                delay(pause)
            }
        }

        val client = baseClient.newBuilder()
            .callTimeout(java.time.Duration.ofSeconds(timeoutSeconds))
            .build()

        var lastIoException: java.io.IOException? = null
        for (attempt in 0..retries) {
            val req = Request.Builder()
                .url(url)
                .header("User-Agent", Cx.UA)
                .apply {
                    headers.forEach { (k, v) -> header(k, v) }
                    if (body != null && method == "POST") post(body)
                    else if (body != null) put(body)
                }
                .build()

            try {
                val resp = withContext(Dispatchers.IO) { client.newCall(req).execute() }
                if (resp.code == 429) {
                    noteRateLimited()
                    if (attempt < retries) {
                        val pauseMs = minOf(30_000L, RATE_LIMIT_BACKOFF_MS * (1L shl attempt))
                        log("请求 ${req.url.encodedPath} 被限流（429），${pauseMs / 1000}s 后重试（${attempt + 1}/$retries）")
                        delay(pauseMs)
                        resp.close()
                        continue
                    }
                    resp.close()
                    throw CrawlException(
                        "教务系统返回限流（HTTP 429），请等待 1~2 分钟后再试；" +
                            "若反复出现，请避开综测统计等高峰时段使用。"
                    )
                }
                if (resp.code in RETRY_STATUS && attempt < retries) {
                    val pauseMs = RETRY_BACKOFF_MS * (1L shl attempt)
                    log("请求 ${req.url.encodedPath} 返回 ${resp.code}，${pauseMs / 1000.0}s 后重试（${attempt + 1}/$retries）")
                    delay(pauseMs)
                    resp.close()
                    continue
                }
                if (resp.code < 400) {
                    mutex.withLock {
                        rateHits = 0
                        cooldownUntil = 0L
                    }
                }
                return resp
            } catch (e: java.io.IOException) {
                lastIoException = e
                if (attempt < retries) {
                    val pauseMs = RETRY_BACKOFF_MS * (1L shl attempt)
                    log("网络异常（${e.javaClass.simpleName}），${pauseMs / 1000.0}s 后重试（${attempt + 1}/$retries）")
                    delay(pauseMs)
                }
            }
        }
        throw CrawlException(
            "网络请求多次失败（$method ${url.toHttpUrl().encodedPath}，" +
                "${lastIoException?.javaClass?.simpleName ?: "IOException"}）。请检查网络后重试。"
        )
    }

    private suspend fun noteRateLimited() {
        mutex.withLock {
            rateHits += 1
            val cooldown = minOf(
                RATE_LIMIT_COOLDOWN_MAX_MS,
                30_000L * (1L shl (rateHits - 1)),
            )
            cooldownUntil = maxOf(cooldownUntil, System.currentTimeMillis() + cooldown)
            log("教务系统限流，已进入冷却：后续 ${cooldown / 1000} 秒内主动放慢请求")
        }
    }

    /** 请求并按 UTF-8 取文本（超星页面为 UTF-8，显式解码）。
     *  整个"执行→读取→关闭"周期都在 IO 线程：响应体未读完就 close 会触发 OkHttp
     *  排空剩余数据（socket 读），绝不能落在主线程。 */
    suspend fun text(
        method: String, url: String, form: Map<String, String>? = null,
        headers: Map<String, String> = emptyMap(),
        retries: Int = DEFAULT_RETRIES, timeoutSeconds: Long = 30,
    ): String {
        val body = form?.let {
            FormBody.Builder().apply { it.forEach { (k, v) -> add(k, v) } }.build()
        }
        return withContext(Dispatchers.IO) {
            request(method, url, body, headers, retries, timeoutSeconds).use { resp ->
                resp.body?.bytes()?.toString(Charsets.UTF_8) ?: ""
            }
        }
    }

    /** 请求并解析 JSON。
     *  超星接口（getauthstatus 等）实测用 text/html 承载 JSON——官方页面 dataType:'json'
     *  也只解析响应体、不校验 content-type，因此这里同样按内容解析，失败才报"非 JSON"。 */
    suspend fun json(
        method: String, url: String, form: Map<String, String>? = null,
        headers: Map<String, String> = emptyMap(),
        retries: Int = DEFAULT_RETRIES, timeoutSeconds: Long = 30,
    ): kotlinx.serialization.json.JsonElement {
        val body = form?.let {
            FormBody.Builder().apply { it.forEach { (k, v) -> add(k, v) } }.build()
        }
        return withContext(Dispatchers.IO) {
            request(method, url, body, headers, retries, timeoutSeconds).use { resp ->
                val bytes = resp.body?.bytes() ?: ByteArray(0)
                val contentType = resp.header("content-type") ?: ""
                val text = bytes.toString(Charsets.UTF_8)
                runCatching { json.parseToJsonElement(text) }.getOrElse {
                    throw CrawlException(
                        "接口返回了非 JSON 内容（HTTP ${resp.code}，${contentType.ifEmpty { "无 content-type" }}，" +
                            "路径 ${resp.request.url.host}${resp.request.url.encodedPath}），" +
                            "可能触发了风控或登录验证。请稍后重试。" + pageTitleHint(text)
                    )
                }
            }
        }
    }

    /** 从非 JSON 响应里提取 <title> 或开头片段，帮助判断被拦截到哪个页面 */
    private fun pageTitleHint(text: String): String {
        val snippet = text.replace(Regex("\\s+"), " ").trim()
        if (snippet.isEmpty()) return ""
        val title = Regex("<title[^>]*>(.*?)</title>", RegexOption.IGNORE_CASE)
            .find(snippet)?.groupValues?.getOrNull(1)?.trim()
        val hint = title?.take(40) ?: snippet.take(60)
        return "（页面：$hint）"
    }

    fun close() {
        baseClient.connectionPool.evictAll()
        baseClient.dispatcher.executorService.shutdown()
    }
}
