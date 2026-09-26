package com.mlw.zongce.ui.jw

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mlw.zongce.data.CookieStore
import com.mlw.zongce.logic.CrawlOutcome
import com.mlw.zongce.logic.SelectParser
import com.mlw.zongce.network.ChaoxingSession
import com.mlw.zongce.network.CrawlException
import com.mlw.zongce.network.Cx
import com.mlw.zongce.network.ScanInfo
import com.mlw.zongce.network.ScanPoll
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** 教务登录/抓取的阶段 */
enum class JwPhase { IDLE, OPENING, WAITING_SCAN, LOGGED_IN, CRAWLING, FINISHED, FAILED }

data class JwProgressUi(val stage: String, val pct: Double?, val detail: String)

data class JwUiState(
    val phase: JwPhase = JwPhase.IDLE,
    val scan: ScanInfo? = null,
    val secondsLeft: Int = 0,
    val scannedNick: String? = null,
    val filters: SelectParser.FilterInfo? = null,
    val yearValue: String? = null,
    val termValue: String? = null,
    val logs: List<String> = emptyList(),
    val progress: JwProgressUi? = null,
    val outcome: CrawlOutcome? = null,
    val error: String? = null,
)

/**
 * 教务扫码登录 + 抓取状态机（对应网页版 scan/jw 流程）。
 * 网络与业务逻辑全部在 [ChaoxingSession]，本类只做状态编排。
 */
class JwViewModel(private val cookieStore: CookieStore?) : ViewModel() {

    private companion object {
        const val TAG = "ZongCeJw"
    }

    private val _state = MutableStateFlow(JwUiState())
    val state: StateFlow<JwUiState> = _state.asStateFlow()

    private val session = ChaoxingSession(
        log = { msg -> appendLog(msg) },
        progress = { stage, pct, detail ->
            _state.value = _state.value.copy(progress = JwProgressUi(stage, pct, detail))
        },
    )

    private var pollJob: Job? = null

    private fun appendLog(msg: String) {
        _state.value = _state.value.copy(logs = (_state.value.logs + msg).takeLast(60))
    }

    /** 首次进入：若本地留有上次会话 cookie，静默尝试直接恢复登录态 */
    init {
        val store = cookieStore
        if (store != null) {
            viewModelScope.launch {
                val snapshot = store.load()
                android.util.Log.d(TAG, "restore: ${snapshot.size} cookies from store")
                if (snapshot.isEmpty()) return@launch
                session.http.cookieJar.restore(snapshot)
                try {
                    applyFilters(session.discoverFilters())
                    appendLog("已恢复上次登录状态")
                    android.util.Log.d(TAG, "restore: ok")
                } catch (e: CrawlException) {
                    // 会话失效：清掉回到扫码态（对应"会话已失效，请重新扫码登录"分支）
                    android.util.Log.d(TAG, "restore: session invalid, cleared (${e.message})")
                    session.http.cookieJar.clear()
                    store.clear()
                }
            }
        }
    }

    private fun applyFilters(filters: SelectParser.FilterInfo) {
        _state.value = _state.value.copy(
            phase = JwPhase.LOGGED_IN,
            filters = filters,
            yearValue = filters.year?.options?.firstOrNull()?.value,
            termValue = filters.term?.options?.firstOrNull()?.value,
        )
    }

    // ---------------- 扫码 ---------------- //

    fun startScan() {
        pollJob?.cancel()
        _state.value = _state.value.copy(phase = JwPhase.OPENING, error = null)
        viewModelScope.launch {
            try {
                val info = session.openScan()
                _state.value = _state.value.copy(
                    phase = JwPhase.WAITING_SCAN,
                    scan = info,
                    scannedNick = null,
                    secondsLeft = Cx.QR_TTL_SECONDS,
                )
                startPollLoop()
            } catch (e: CrawlException) {
                _state.value = _state.value.copy(phase = JwPhase.FAILED, error = e.message)
            }
        }
    }

    fun refreshScan() {
        pollJob?.cancel()
        _state.value = _state.value.copy(phase = JwPhase.OPENING, error = null)
        viewModelScope.launch {
            try {
                val info = session.refreshScan()
                _state.value = _state.value.copy(
                    phase = JwPhase.WAITING_SCAN,
                    scan = info,
                    scannedNick = null,
                    secondsLeft = Cx.QR_TTL_SECONDS,
                )
                startPollLoop()
            } catch (e: CrawlException) {
                _state.value = _state.value.copy(phase = JwPhase.FAILED, error = e.message)
            }
        }
    }

    private fun startPollLoop() {
        pollJob = viewModelScope.launch {
            while (isActive) {
                when (val poll = session.pollState()) {
                    is ScanPoll.Waiting -> {
                        if (poll.reset) {
                            _state.value = _state.value.copy(scan = session.currentScan)
                        }
                        _state.value = _state.value.copy(secondsLeft = session.ttlLeftSeconds())
                    }
                    is ScanPoll.Scanned -> _state.value = _state.value.copy(
                        scannedNick = poll.nick.ifEmpty { null },
                        secondsLeft = session.ttlLeftSeconds(),
                    )
                    is ScanPoll.Confirmed -> {
                        onLoginConfirmed()
                        return@launch
                    }
                    is ScanPoll.Expired -> _state.value = _state.value.copy(
                        phase = JwPhase.FAILED,
                        error = "二维码已失效，请刷新重试",
                    )
                    is ScanPoll.Error -> {
                        _state.value = _state.value.copy(phase = JwPhase.FAILED, error = poll.message)
                        return@launch
                    }
                    ScanPoll.Idle -> Unit
                }
                delay(Cx.POLL_INTERVAL_MS)
            }
        }
    }

    private suspend fun onLoginConfirmed() {
        try {
            applyFilters(session.discoverFilters())
            cookieStore?.save(session.http.cookieJar.snapshot())
        } catch (e: CrawlException) {
            _state.value = _state.value.copy(phase = JwPhase.FAILED, error = e.message)
        }
    }

    // ---------------- 抓取 ---------------- //

    fun setYear(v: String) {
        _state.value = _state.value.copy(yearValue = v)
    }

    fun setTerm(v: String) {
        _state.value = _state.value.copy(termValue = v)
    }

    fun startCrawl() {
        val s = _state.value
        val year = s.yearValue ?: return
        val term = s.termValue ?: return
        _state.value = s.copy(
            phase = JwPhase.CRAWLING,
            progress = JwProgressUi("开始抓取", null, ""),
            error = null,
            outcome = null,
        )
        viewModelScope.launch {
            try {
                val outcome = session.crawl(year, term, s.filters)
                _state.value = _state.value.copy(phase = JwPhase.FINISHED, outcome = outcome)
            } catch (e: CrawlException) {
                _state.value = _state.value.copy(
                    phase = JwPhase.FAILED,
                    error = e.message,
                    progress = null,
                )
            }
        }
    }

    fun logout() {
        pollJob?.cancel()
        viewModelScope.launch {
            session.logout()
            cookieStore?.clear()
            _state.value = JwUiState()
        }
    }

    /** 从失败态返回：还有登录态就回到抓取页，否则回到首页 */
    fun dismissError() {
        if (_state.value.phase != JwPhase.FAILED) return
        _state.value = _state.value.copy(
            phase = if (_state.value.filters != null) JwPhase.LOGGED_IN else JwPhase.IDLE,
            error = null,
        )
    }

    override fun onCleared() {
        pollJob?.cancel()
        super.onCleared()
    }
}
