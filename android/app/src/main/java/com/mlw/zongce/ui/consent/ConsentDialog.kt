package com.mlw.zongce.ui.consent

import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable

/** 首启合规声明（文案对齐网页版 consent 弹窗）。不同意则退出应用。 */
@Composable
fun ConsentDialog(
    onAccept: () -> Unit,
    onDecline: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDecline,
        title = { Text("使用前必读") },
        text = {
            Text(
                "本工具仅限查询本人学业成绩：\n" +
                    "1. 必须用本人学习通账号扫码登录；\n" +
                    "2. 禁止查询、抓取任何他人的成绩信息；\n" +
                    "3. 不得高频请求，遵守学校教务系统使用规定；\n" +
                    "4. 违规使用造成的一切后果由使用者本人承担。\n\n" +
                    "不同意请退出，勿继续使用。",
            )
        },
        confirmButton = {
            TextButton(onClick = onAccept) { Text("我已阅读并同意") }
        },
        dismissButton = {
            TextButton(onClick = onDecline) { Text("不同意并退出") }
        },
    )
}
