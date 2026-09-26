package com.mlw.zongce.ui.calculator

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.mlw.zongce.data.CookieStore
import com.mlw.zongce.logic.CalcEngine
import com.mlw.zongce.logic.CalcResult
import com.mlw.zongce.logic.CalcState
import com.mlw.zongce.logic.CourseField
import com.mlw.zongce.logic.GradeImport
import com.mlw.zongce.ui.jw.JwPhase
import com.mlw.zongce.ui.jw.JwSheet
import com.mlw.zongce.ui.jw.JwViewModel
import kotlinx.coroutines.launch

/**
 * 计算器主页。界面文案与网页版 static/index.html 对齐：
 * 「综合测评分 / 满分 93.00 分 / 专业学习成绩 / 其他几项 / 复制结果 / 全部清空」。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CalculatorScreen(viewModel: CalculatorViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    // 教务抓取入口（M3）
    val context = LocalContext.current
    val jwVm: JwViewModel = viewModel { JwViewModel(CookieStore(context.applicationContext)) }
    val jwState by jwVm.state.collectAsStateWithLifecycle()
    var showJwSheet by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text("综测计算器") },
                actions = {
                    TextButton(onClick = { showJwSheet = true }) {
                        Text(
                            when (jwState.phase) {
                                JwPhase.LOGGED_IN, JwPhase.CRAWLING, JwPhase.FINISHED -> "已登录·抓取"
                                else -> "扫码抓成绩"
                            }
                        )
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        val current = state
        if (current == null) {
            Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center,
            ) { CircularProgressIndicator() }
            return@Scaffold
        }

        val result = remember(current) { CalcEngine.compute(current) }
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp)
                .padding(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Spacer(Modifier.height(2.dp))
            TotalCard(result)

            SectionCard(title = "专业学习成绩") {
                current.courses.forEachIndexed { index, entry ->
                    CourseRow(
                        entry = entry,
                        onScore = { viewModel.onCourseInput(index, CourseField.SCORE, it) },
                        onCredit = { viewModel.onCourseInput(index, CourseField.CREDIT, it) },
                        onDelete = { viewModel.onDeleteCourse(index) },
                    )
                }
                TextButton(onClick = { viewModel.onAddCourse() }) {
                    Text("＋ 加一门课")
                }
                Text(
                    "填了成绩和学分的专业课会计入加权平均；体育等不纳入的课程请勿填写，或留空学分。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            SectionCard(title = "其他几项") {
                LabeledField("思想品德考评分", current.moral, "0 – 100") {
                    viewModel.onFieldChange("moral", it)
                }
                LabeledField("宿舍平均分", current.labor, "0 – 100") {
                    viewModel.onFieldChange("labor", it)
                }
                LabeledField("PU 分", current.pu, "如 8.5") {
                    viewModel.onFieldChange("pu", it)
                }
                LabeledField("体育总评", current.sport, "0 – 100") {
                    viewModel.onFieldChange("sport", it)
                }
                LabeledField("心理总评", current.psych, "0 – 100") {
                    viewModel.onFieldChange("psych", it)
                }
            }

            ActionsRow(
                result = result,
                state = current,
                snackbar = snackbar,
                scope = scope,
                onReset = viewModel::onReset,
            )

            FormulaCard()
        }
    }

    if (showJwSheet) {
        JwSheet(
            state = jwState,
            vm = jwVm,
            onDismiss = { showJwSheet = false },
            onImported = { outcome ->
                viewModel.importGrades(outcome)
                showJwSheet = false
                scope.launch {
                    snackbar.showSnackbar(
                        "已导入 ${GradeImport.numericCount(outcome)} 门数字成绩" +
                            (if (GradeImport.gradedCount(outcome) > 0) "，${GradeImport.gradedCount(outcome)} 门等级成绩单列" else "")
                    )
                }
            },
            hasFilledCourses = state?.courses
                ?.any { it.score.isNotEmpty() || it.credit.isNotEmpty() } ?: false,
        )
    }
}

@Composable
private fun TotalCard(result: CalcResult) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.primaryContainer,
    ) {
        Column(Modifier.fillMaxWidth().padding(20.dp)) {
            Text(
                "综合测评分",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
            )
            Text(
                CalcEngine.format(result.total),
                fontSize = 44.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
            )
            Text(
                "满分 ${CalcEngine.format(CalcEngine.FULL_SCORE)} 分",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
            )
        }
    }
}

@Composable
private fun SectionCard(
    title: String,
    content: @Composable ColumnScope.() -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(),
    ) {
        Column(
            Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            content()
        }
    }
}

@Composable
private fun CourseRow(
    entry: com.mlw.zongce.logic.CourseEntry,
    onScore: (String) -> Unit,
    onCredit: (String) -> Unit,
    onDelete: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        OutlinedTextField(
            value = entry.score,
            onValueChange = onScore,
            modifier = Modifier.weight(1.4f),
            label = { Text("成绩") },
            placeholder = { Text("如 88") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        )
        OutlinedTextField(
            value = entry.credit,
            onValueChange = onCredit,
            modifier = Modifier.weight(1f),
            label = { Text("学分") },
            placeholder = { Text("如 3") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        )
        IconButton(onClick = onDelete) {
            Icon(
                Icons.Outlined.Delete,
                contentDescription = "删除该行",
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun LabeledField(
    label: String,
    value: String,
    placeholder: String,
    onValueChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        label = { Text(label) },
        placeholder = { Text(placeholder) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
    )
}

@Composable
private fun ActionsRow(
    result: CalcResult,
    state: CalcState,
    snackbar: SnackbarHostState,
    scope: kotlinx.coroutines.CoroutineScope,
    onReset: () -> Unit,
) {
    val clipboard = LocalClipboardManager.current
    var confirmReset by remember { mutableStateOf(false) }

    if (confirmReset) {
        AlertDialog(
            onDismissRequest = { confirmReset = false },
            title = { Text("全部清空") },
            text = { Text("确定清空全部内容？清空后无法恢复。") },
            confirmButton = {
                TextButton(onClick = {
                    confirmReset = false
                    onReset()
                    scope.launch { snackbar.showSnackbar("已清空") }
                }) { Text("清空") }
            },
            dismissButton = {
                TextButton(onClick = { confirmReset = false }) { Text("取消") }
            },
        )
    }

    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedButton(
            onClick = {
                clipboard.setText(AnnotatedString(buildShareText(result)))
                scope.launch { snackbar.showSnackbar("已复制") }
            },
            modifier = Modifier.weight(1f),
        ) {
            Icon(Icons.Outlined.ContentCopy, contentDescription = null, Modifier.size(18.dp))
            Spacer(Modifier.size(6.dp))
            Text("复制结果")
        }
        OutlinedButton(
            onClick = { confirmReset = true },
            modifier = Modifier.weight(1f),
        ) {
            Text("全部清空")
        }
    }
}

/** 复制格式与网页版 copyBtn 逐字对齐。 */
internal fun buildShareText(result: CalcResult): String = listOf(
    "综合测评分：${CalcEngine.format(result.total)} / ${CalcEngine.format(CalcEngine.FULL_SCORE)}",
    "",
    "专业学习测评：${CalcEngine.format(result.academic)}" +
        "（Σ成绩×学分 ${CalcEngine.format(result.sumWC)} ÷ 学分合计 ${CalcEngine.format(result.sumC)}）",
    "思想品德：${CalcEngine.format(result.moral)}",
    "劳动素养测评：${CalcEngine.format(result.labor)}",
    "能力发展测评：${CalcEngine.format(result.ability)}",
    "身心素质：${CalcEngine.format(result.body)}",
).joinToString("\n")

@Composable
private fun FormulaCard() {
    var expanded by remember { mutableStateOf(false) }
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            androidx.compose.material3.ListItem(
                headlineContent = { Text("查看计算公式") },
                trailingContent = {
                    IconButton(onClick = { expanded = !expanded }) {
                        Icon(
                            if (expanded) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                            contentDescription = null,
                        )
                    }
                },
            )
            AnimatedVisibility(expanded) {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    FormulaLine("综合测评分 = 0.60 × 专业学习测评 + 0.20 × 思想品德 + 0.10 × 劳动素养测评 + 0.05 × 能力发展测评 + 0.05 × 身心素质")
                    FormulaLine("专业学习测评 = Σ(各学科总评分 × 对应学分) ÷ Σ学分")
                    FormulaLine("思想品德 = 考评分 × 0.8")
                    FormulaLine("劳动素养测评 = 宿舍平均分 × 0.8")
                    FormulaLine("能力发展测评 = PU 分 × 10（超过 100 按 100 计）")
                    FormulaLine("身心素质 = 体育总评 × 0.8 × 0.5 + 心理总评 × 0.8 × 0.5")
                    Text(
                        "各项均以满分 100 分计，本公式理论上限为 93.00 分；心理总评默认按 100 分计。\n" +
                            "结果仅供参考，最终成绩以学校公布为准。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun FormulaLine(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurface,
    )
}
