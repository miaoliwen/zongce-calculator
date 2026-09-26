package com.mlw.zongce.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.mlw.zongce.logic.CalcState
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.flow.map
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

private val Context.zongceDataStore by preferencesDataStore(name = "zongce")

/**
 * 计算器状态持久化。
 * 语义对齐网页版 localStorage（key: zongce_simple_v1）：JSON 全量读写，
 * 坏数据/缺数据时回退到全新状态。
 */
class CalcStateStore(private val context: Context) {

    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
    }

    private val key = stringPreferencesKey("calc_state_v1")

    val state: Flow<CalcState> = context.zongceDataStore.data.map { prefs ->
        prefs[key]?.let { raw ->
            runCatching { json.decodeFromString<CalcState>(raw) }.getOrNull()
        } ?: CalcState()
    }

    suspend fun save(state: CalcState) {
        context.zongceDataStore.edit { it[key] = json.encodeToString(state) }
    }
}

/** 合规声明同意标记（对应网页版 CONSENT_KEY）。 */
class ConsentStore(private val context: Context) {

    private val key = booleanPreferencesKey("consent_v1")

    /** 合规声明同意标记（对应网页版 CONSENT_KEY）。
     *  键不存在（从未同意过）必须返回 false 触发首启弹窗；
     *  返回 null 会被 MainActivity 当作"加载中"而渲染空白屏。 */
    val accepted: Flow<Boolean> = context.zongceDataStore.data.map { prefs ->
        prefs[key] ?: false
    }

    suspend fun setAccepted(value: Boolean) {
        context.zongceDataStore.edit { it[key] = value }
    }
}

/** 教务会话 cookie 持久化：App 重启后静默恢复登录态（失效则清空）。 */
class CookieStore(private val context: Context) {

    private val json = Json { ignoreUnknownKeys = true }
    private val key = stringPreferencesKey("jw_cookies_v1")

    suspend fun load(): List<com.mlw.zongce.network.CookieSnapshot> {
        val raw = context.zongceDataStore.data.map { it[key] }.firstOrNull() ?: return emptyList()
        return runCatching {
            json.decodeFromString<List<com.mlw.zongce.network.CookieSnapshot>>(raw)
        }.getOrDefault(emptyList())
    }

    suspend fun save(snapshots: List<com.mlw.zongce.network.CookieSnapshot>) {
        context.zongceDataStore.edit { it[key] = json.encodeToString(snapshots) }
    }

    suspend fun clear() {
        context.zongceDataStore.edit { it.remove(key) }
    }
}
