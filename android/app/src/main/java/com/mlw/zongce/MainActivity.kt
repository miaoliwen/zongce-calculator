package com.mlw.zongce

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.mlw.zongce.data.CalcStateStore
import com.mlw.zongce.data.ConsentStore
import com.mlw.zongce.ui.calculator.CalculatorScreen
import com.mlw.zongce.ui.calculator.CalculatorViewModel
import com.mlw.zongce.ui.consent.ConsentDialog
import com.mlw.zongce.ui.theme.ZongCeTheme
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val calcStore = CalcStateStore(applicationContext)
        val consentStore = ConsentStore(applicationContext)

        setContent {
            ZongCeTheme {
                // null = 合规标记尚未加载，避免误弹/漏弹
                val consentAccepted by consentStore.accepted
                    .collectAsStateWithLifecycle(initialValue = null)
                val scope = rememberCoroutineScope()
                when (consentAccepted) {
                    null -> Unit
                    false -> ConsentDialog(
                        onAccept = { scope.launch { consentStore.setAccepted(true) } },
                        onDecline = { finish() },
                    )
                    true -> {
                        val vm: CalculatorViewModel =
                            viewModel { CalculatorViewModel(calcStore) }
                        CalculatorScreen(vm)
                    }
                }
            }
        }
    }
}
