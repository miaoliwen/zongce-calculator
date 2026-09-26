package com.mlw.zongce.ui.jw

import android.app.Activity
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.widget.Toast
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mlw.zongce.logic.CrawlOutcome
import com.mlw.zongce.logic.GradeImport
import com.mlw.zongce.network.Cx

/**
 * 教务登录/抓取 BottomSheet。
 * 登录主路径：M0 验证的确认页 deep link（uuid/enc 直接构造）；
 * 二维码图片仅作跨设备兜底。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun JwSheet(
    state: JwUiState,
    vm: JwViewModel,
    onDismiss: () -> Unit,
    onImported: (CrawlOutcome) -> Unit,
    hasFilledCourses: Boolean,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp)
                .padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            when (state.phase) {
                JwPhase.IDLE -> IdleContent(vm)
                JwPhase.OPENING -> CenterBox { CircularProgressIndicator() }
                JwPhase.WAITING_SCAN -> WaitingScanContent(state, vm)
                JwPhase.LOGGED_IN -> LoggedInContent(state, vm, hasFilledCourses)
                JwPhase.CRAWLING -> CrawlingContent(state)
                JwPhase.FINISHED -> FinishedContent(state, vm, onImported)
                JwPhase.FAILED -> FailedContent(state, vm)
            }
        }
    }
}

// ---------------- 阶段内容 ---------------- //

@Composable
private fun IdleContent(vm: JwViewModel) {
    Text("扫码登录教务系统", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
    Text(
        "用学习通确认登录后，可自动抓取本人各门课程的成绩与学分并填入计算器。" +
            "仅支持学习通扫码（账密登录已关停）。",
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Button(onClick = { vm.startScan() }, modifier = Modifier.fillMaxWidth()) {
        Text("扫码登录教务系统", fontSize = 16.sp)
    }
    Text(
        "仅限查询本人学业成绩；请保持低频使用，遵守教务系统规定。",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun WaitingScanContent(state: JwUiState, vm: JwViewModel) {
    val context = LocalContext.current
    val scan = state.scan

    Text("学习通登录确认", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)

    if (scan != null) {
        Button(
            onClick = { openDeepLink(context, scan.deepLink) },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("打开学习通确认登录", fontSize = 16.sp)
        }
        Text(
            "点击上方按钮，在学习通 App 或浏览器中确认登录；确认后本页会自动继续。" +
                "若无反应，可将下方二维码保存到相册，用学习通「扫一扫 → 相册」识别。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        scan.qrBytes?.let { bytes ->
            val bitmap = remember(bytes) { BitmapFactory.decodeByteArray(bytes, 0, bytes.size) }
            if (bitmap != null) {
                Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
                    Image(
                        bitmap = bitmap.asImageBitmap(),
                        contentDescription = "登录二维码（供另一台设备扫描）",
                        modifier = Modifier.size(180.dp).clip(RoundedCornerShape(12.dp)),
                    )
                    Text(
                        "跨设备兜底：用其他手机的学习通扫描此码",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (Build.VERSION.SDK_INT >= 29) {
                        TextButton(onClick = {
                            val ok = saveQrToGallery(context, bytes)
                            Toast.makeText(
                                context,
                                if (ok) "二维码已保存到相册 Pictures/ZongCe" else "保存失败",
                                Toast.LENGTH_SHORT,
                            ).show()
                        }) { Text("保存二维码到相册") }
                    }
                }
            }
        }
    }

    StatusLine(state)

    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedButton(onClick = { vm.refreshScan() }, modifier = Modifier.weight(1f)) {
            Text("刷新二维码")
        }
        OutlinedButton(onClick = { vm.logout() }, modifier = Modifier.weight(1f)) {
            Text("取消")
        }
    }
}

@Composable
private fun StatusLine(state: JwUiState) {
    val nick = state.scannedNick
    val status = when {
        nick != null -> "已扫码（$nick），请在手机上确认…"
        else -> "等待手机确认中…"
    }
    Text(
        status + "　剩余 ${state.secondsLeft}s",
        style = MaterialTheme.typography.bodyMedium,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.fillMaxWidth(),
        textAlign = androidx.compose.ui.text.style.TextAlign.Center,
    )
}

@Composable
private fun LoggedInContent(state: JwUiState, vm: JwViewModel, hasFilledCourses: Boolean) {
    Text("✓ 已登录教务系统", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
    Text(
        "选择学年/学期后开始抓取（可选「全部」）。",
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    val filters = state.filters
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        OptionPicker(
            label = "学年",
            options = filters?.year?.options ?: emptyList(),
            selected = state.yearValue,
            enabled = filters?.year != null,
            modifier = Modifier.weight(1f),
            onPick = vm::setYear,
        )
        OptionPicker(
            label = "学期",
            options = filters?.term?.options ?: emptyList(),
            selected = state.termValue,
            enabled = filters?.term != null,
            modifier = Modifier.weight(1f),
            onPick = vm::setTerm,
        )
    }

    // 抓取会覆盖当前课程行（对齐网页版覆盖确认）
    var confirmOverwrite by remember { mutableStateOf(false) }
    if (confirmOverwrite) {
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { confirmOverwrite = false },
            title = { Text("覆盖确认") },
            text = { Text("抓取结果将覆盖当前成绩列表，是否继续？") },
            confirmButton = {
                TextButton(onClick = {
                    confirmOverwrite = false
                    vm.startCrawl()
                }) { Text("继续抓取") }
            },
            dismissButton = {
                TextButton(onClick = { confirmOverwrite = false }) { Text("取消") }
            },
        )
    }

    Button(
        onClick = {
            if (hasFilledCourses) confirmOverwrite = true else vm.startCrawl()
        },
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text("开始抓取", fontSize = 16.sp)
    }

    TextButton(onClick = { vm.logout() }, modifier = Modifier.fillMaxWidth()) {
        Text("退出登录")
    }
}

@Composable
private fun OptionPicker(
    label: String,
    options: List<com.mlw.zongce.logic.SelectParser.SelectOption>,
    selected: String?,
    enabled: Boolean,
    modifier: Modifier = Modifier,
    onPick: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val currentText = options.firstOrNull { it.value == selected }?.text ?: "请选择"
    Column(modifier) {
        Text(label, style = MaterialTheme.typography.labelMedium)
        Box {
            OutlinedButton(
                onClick = { if (enabled) expanded = true },
                enabled = enabled,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(currentText, maxLines = 1, fontSize = 13.sp)
            }
            androidx.compose.material3.DropdownMenu(
                expanded = expanded,
                onDismissRequest = { expanded = false },
            ) {
                options.forEach { o ->
                    androidx.compose.material3.DropdownMenuItem(
                        text = { Text(o.text, fontSize = 13.sp) },
                        onClick = {
                            expanded = false
                            onPick(o.value)
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun CrawlingContent(state: JwUiState) {
    Text("抓取成绩", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
    val p = state.progress
    val pct = p?.pct
    if (pct != null && pct >= 0) {
        LinearProgressIndicator(
            progress = { (pct / 100.0).toFloat() },
            modifier = Modifier.fillMaxWidth(),
        )
    } else {
        LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
    }
    Text(
        (p?.stage ?: "抓取中") + (p?.detail?.takeIf { it.isNotEmpty() }?.let { " · $it" } ?: "") +
            (pct?.takeIf { it >= 0 }?.let { "（${it.toInt()}%）" } ?: ""),
        style = MaterialTheme.typography.bodyMedium,
    )

    if (state.logs.isNotEmpty()) {
        Surface(
            shape = RoundedCornerShape(10.dp),
            color = MaterialTheme.colorScheme.surfaceVariant,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(Modifier.padding(10.dp)) {
                state.logs.takeLast(6).forEach { line ->
                    Text(
                        line,
                        fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
    Text(
        "正在查询教务系统，请保持页面打开…",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun FinishedContent(
    state: JwUiState,
    vm: JwViewModel,
    onImported: (CrawlOutcome) -> Unit,
) {
    val outcome = state.outcome
    LaunchedEffect(outcome) {
        if (outcome != null) onImported(outcome)
    }
    if (outcome == null) return

    val numeric = GradeImport.numericCount(outcome)
    val graded = GradeImport.gradedCount(outcome)
    val excluded = outcome.excluded

    Text("抓取完成", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
    Text(
        "已导入 $numeric 门数字成绩，综测总分已更新。" +
            (if (graded > 0) "另有 $graded 门等级成绩（不计入加权）。" else "") +
            (if (excluded.isNotEmpty()) "剔除 ${excluded.size} 门不纳入专业成绩的课程。" else ""),
        style = MaterialTheme.typography.bodyMedium,
    )

    if (graded > 0) {
        NoteBlock(
            title = "等级成绩课程（不计入加权）",
            lines = outcome.rows.filter { it.score == null }.map(GradeImport::gradedDesc),
        )
    }
    if (excluded.isNotEmpty()) {
        NoteBlock(
            title = "已剔除（不纳入专业成绩）",
            lines = excluded.map(GradeImport::excludedDesc),
        )
    }

    Button(onClick = { vm.dismissError() }, modifier = Modifier.fillMaxWidth()) {
        Text("完成")
    }
}

@Composable
private fun NoteBlock(title: String, lines: List<String>) {
    Surface(
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(title, style = MaterialTheme.typography.labelLarge)
            lines.forEach {
                Text(it, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun FailedContent(state: JwUiState, vm: JwViewModel) {
    Text("出错了", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
    Text(
        state.error ?: "未知错误",
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.error,
    )
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        if (state.filters != null) {
            OutlinedButton(onClick = { vm.dismissError() }, modifier = Modifier.weight(1f)) {
                Text("返回")
            }
            Button(onClick = { vm.startCrawl() }, modifier = Modifier.weight(1f)) {
                Text("重试抓取")
            }
        } else {
            OutlinedButton(onClick = { vm.dismissError() }, modifier = Modifier.weight(1f)) {
                Text("关闭")
            }
            Button(onClick = { vm.startScan() }, modifier = Modifier.weight(1f)) {
                Text("重新登录")
            }
        }
    }
}

// ---------------- 工具 ---------------- //

@Composable
private fun CenterBox(content: @Composable () -> Unit) {
    Box(
        Modifier.fillMaxWidth().heightIn(min = 120.dp),
        contentAlignment = Alignment.Center,
    ) { content() }
}

/** 打开确认页 deep link：学习通 App Links 或浏览器均可承接 */
internal fun openDeepLink(context: Context, url: String): Boolean = try {
    context.startActivity(
        Intent(Intent.ACTION_VIEW, Uri.parse(url))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    )
    true
} catch (e: Exception) {
    false
}

/** 二维码保存到相册（API 29+ 免权限；更早版本不提供该按钮） */
internal fun saveQrToGallery(context: Context, bytes: ByteArray): Boolean {
    if (Build.VERSION.SDK_INT < 29) return false
    val values = ContentValues().apply {
        put(MediaStore.Images.Media.DISPLAY_NAME, "zongce_login_qr_${System.currentTimeMillis()}.jpg")
        put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
        put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/ZongCe")
    }
    val uri = context.contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
        ?: return false
    return try {
        context.contentResolver.openOutputStream(uri)?.use { it.write(bytes) } != null
    } catch (e: Exception) {
        false
    }
}
